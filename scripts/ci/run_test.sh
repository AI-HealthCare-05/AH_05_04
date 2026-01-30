set -eo pipefail

COLOR_GREEN=$(tput setaf 2)
COLOR_BLUE=$(tput setaf 4)
COLOR_RED=$(tput setaf 1)
COLOR_NC=$(tput sgr0)

cd "$(dirname "$0")/.."

echo "${COLOR_BLUE}Find Tests${COLOR_NC}"

HAS_TESTS=false
if [ -d "./app/tests" ] && find ./app/tests -name 'test_*.py' -print -quit | read ; then
  HAS_TESTS=true
fi

echo "Has tests: $HAS_TESTS"

if [ "$HAS_TESTS" = true ]; then
  echo "${COLOR_BLUE}Run Pytest with Coverage${COLOR_NC}"

  if ! uv run coverage run -m pytest app; then
    echo ""
    echo "${COLOR_RED}✖ Pytest failed.${COLOR_NC}"
    echo "${COLOR_RED}→ Fix the test failures above and re-run.${COLOR_NC}"
    exit 1
  fi

  echo "${COLOR_BLUE}Coverage Report${COLOR_NC}"
  if ! uv run coverage report -m ; then
    echo "${COLOR_RED}✖ Coverage check failed.${COLOR_NC}"
    exit 1
  fi
else
  echo "${COLOR_BLUE}No tests found. Skipping tests.${COLOR_NC}"
fi

echo "${COLOR_GREEN}Successfully Ended.${COLOR_NC}"
