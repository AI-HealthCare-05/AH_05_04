#!/usr/bin/env bash

_terminate_parallel_test_process_group() {
  local group_leader_pid="$1"

  kill -TERM -- "-$group_leader_pid" 2>/dev/null || kill -TERM "$group_leader_pid" 2>/dev/null || true
}

_signal_parallel_test_lanes() {
  local requested_status="$1"
  local lane_pid
  shift

  if [ "$signal_status" -eq 0 ]; then
    signal_status="$requested_status"
  fi

  for lane_pid in "$@"; do
    if [ -n "$lane_pid" ]; then
      _terminate_parallel_test_process_group "$lane_pid"
    fi
  done
}

_restore_parallel_test_trap() {
  local previous_trap="$1"
  local signal_name="$2"

  trap - "$signal_name"
  if [ -n "$previous_trap" ]; then
    eval "$previous_trap"
  fi
}

# Run named shell functions concurrently and wait for every lane before returning.
run_parallel_test_lanes() {
  local -a lane_names=("$@")
  local -a lane_pids=()
  local -a lane_logs=()
  local failed=0
  local signal_status=0
  local index
  local lane_log
  local lane_name
  local lane_status
  local lane_pid
  local previous_hup_trap
  local previous_int_trap
  local previous_term_trap
  local caller_monitor_mode=false

  case "$-" in
    *m*) caller_monitor_mode=true ;;
    *)
      set -m
      ;;
  esac

  previous_hup_trap="$(trap -p HUP)"
  previous_int_trap="$(trap -p INT)"
  previous_term_trap="$(trap -p TERM)"

  trap '_signal_parallel_test_lanes 129 "${lane_pids[@]-}"' HUP
  trap '_signal_parallel_test_lanes 130 "${lane_pids[@]-}"' INT
  trap '_signal_parallel_test_lanes 143 "${lane_pids[@]-}"' TERM

  if [ -n "${PARALLEL_TEST_LOG_DIR:-}" ]; then
    mkdir -p "$PARALLEL_TEST_LOG_DIR"
  fi

  for lane_name in "${lane_names[@]}"; do
    if [ "$signal_status" -ne 0 ]; then
      break
    fi

    echo "Start test lane: $lane_name"
    if [ -n "${PARALLEL_TEST_LOG_DIR:-}" ]; then
      lane_log="$PARALLEL_TEST_LOG_DIR/$lane_name.log"
      "$lane_name" >"$lane_log" 2>&1 &
      lane_logs+=("$lane_log")
    else
      "$lane_name" &
      lane_logs+=("")
    fi
    lane_pids+=("$!")

    if [ "$signal_status" -ne 0 ]; then
      lane_pid="${lane_pids[${#lane_pids[@]} - 1]}"
      _terminate_parallel_test_process_group "$lane_pid"
      break
    fi
  done

  # 이미 시작된 lane은 각 process group을 유지하므로 wait 중 job-control 출력만 억제합니다.
  set +m

  for ((index = 0; index < ${#lane_pids[@]}; index += 1)); do
    lane_name="${lane_names[$index]}"
    if wait "${lane_pids[$index]}"; then
      lane_status=0
    else
      lane_status="$?"
      failed=1
    fi

    lane_log="${lane_logs[$index]}"
    if [ -n "$lane_log" ]; then
      cat "$lane_log"
    fi

    if [ "$signal_status" -ne 0 ]; then
      echo "Test lane interrupted: $lane_name (exit $lane_status)"
    elif [ "$lane_status" -eq 0 ]; then
      echo "Test lane passed: $lane_name"
    else
      echo "Test lane failed: $lane_name (exit $lane_status)"
    fi
  done

  _restore_parallel_test_trap "$previous_hup_trap" HUP
  _restore_parallel_test_trap "$previous_int_trap" INT
  _restore_parallel_test_trap "$previous_term_trap" TERM
  if [ "$caller_monitor_mode" = true ]; then
    set -m
  fi

  if [ "$signal_status" -ne 0 ]; then
    return "$signal_status"
  fi

  return "$failed"
}

run_parallel_test_lanes_with_failure_summary() {
  local lane_status

  if run_parallel_test_lanes "$@"; then
    return 0
  else
    lane_status="$?"
  fi

  echo
  if [ "$lane_status" -eq 1 ]; then
    echo "One or more parallel test lanes failed."
  else
    echo "Parallel test lanes interrupted (exit $lane_status)."
  fi
  return "$lane_status"
}
