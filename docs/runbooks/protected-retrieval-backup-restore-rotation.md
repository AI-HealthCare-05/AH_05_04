# Protected Retrieval Backup, Restore, and Rotation Runbook

This runbook applies only to the reviewer-gated `protected-retrieval` GitHub Environment and
the manual `protected_retrieval_runner.yml` workflow. It does not authorize production work
during the implementation pull request. Actual operations begin only after merge and explicit
operator approval.

## Required protected configuration names

- SSH boundary: `EC2_HOST`, `EC2_USER`, `EC2_SSH_KEY`, `EC2_SSH_KNOWN_HOSTS`
- database administration: `DB_ADMIN_USER`, `DB_ADMIN_PASSWORD`
- protected schema and roles: `PROTECTED_DB_SCHEMA`, `PROTECTED_DB_OWNER_ROLE`,
  `PROTECTED_DB_ACCESS_ROLE`, `PROTECTED_DB_CONTROL_ROLE`
- DATA identity: `PROTECTED_DB_USER`, `PROTECTED_DB_PASSWORD`
- CONTROL identity: `PROTECTED_DB_CONTROL_USER`, `PROTECTED_DB_CONTROL_PASSWORD`
- backup identity: `PROTECTED_DB_BACKUP_USER`, `PROTECTED_DB_BACKUP_PASSWORD`
- backup storage and encryption: `PROTECTED_BACKUP_HOST_ROOT`,
  `PROTECTED_BACKUP_ENCRYPTION_KEY`, `PROTECTED_BACKUP_ENCRYPTION_KEY_VERSION`,
  `PROTECTED_BACKUP_ACL_POLICY_ID`
- temporary rotation values: `PROTECTED_DB_PASSWORD_NEXT`,
  `PROTECTED_DB_CONTROL_PASSWORD_NEXT`

Secret values belong only in the protected Environment or the approved host secret store.
Never place them in repository files, workflow inputs, issue comments, receipts, shell command
arguments, or copied terminal transcripts. Keep shell history disabled for credential-handling
sessions and use interactive/stdin secret entry rather than command-line values.

## Encrypted backup

1. Confirm protected Environment approval and the temporary runner `/32` SSH rule.
2. Confirm the protected backup directory is owned by the operator account, is not a symlink,
   and has mode `0700`. Existing artifacts must be mode `0600`.
3. Run `provision` when the backup login is new or its policy was intentionally reconciled.
4. Dispatch `backup` with one reason (`DAILY`, `FREEZE`, `VERSION_CHANGE`, or
   `EVIDENCE_REHEARSAL`) and a non-sensitive logical `scope_ref`.
5. Retain only the emitted JSON receipt. It identifies a logical backup and an encrypted
   digest; it must not reveal a host path, database endpoint, nonce, tag, key, or row content.
6. Remove the temporary runner `/32` rule immediately after the job.

The operation validates the exact nine-relation scope and `sha256_hex` domain before executing
`pg_dump --format=custom --no-owner --no-acl`. Plaintext flows from `pg_dump` stdout into the
AES-256-GCM encryptor and is never written as a persistent or temporary dump file.

## Isolated restore verification and cleanup

1. Select only the logical `backup_id` from a successful backup receipt. Never supply a path,
   endpoint, credential, or key as workflow input.
2. Dispatch `restore-verify`. The workflow generates and masks a fresh disposable database
   password, starts the fixed `protected-retrieval-restore-db` alias without ports or a
   persistent volume, and mounts backup storage read-only into the verifier.
3. The verifier authenticates the encrypted artifact before streaming a second decryption pass
   to `pg_restore` stdin. It rejects every target except the fixed isolated alias.
4. Confirm the receipt reports the production guard, Alembic head, exact relations, audit
   integrity, ACL validation, and cleanup as `PASS`.
5. The workflow trap removes the disposable database on success and failure. If a runner is
   interrupted, use the same Compose project and protected-restore profile to remove only
   `protected-retrieval-restore-db`, then remove the temporary remote env file.
6. Remove the temporary runner `/32` rule immediately after the job.

Never point the verifier at `postgres`, `localhost`, `127.0.0.1`, a production endpoint, or an
operator-supplied hostname. The disposable credential is not a production credential.
`acl_validation` proves that the isolated restored schema was hardened to owner-only object ACLs
with no PUBLIC or unrelated-role grants. Live production DATA/CONTROL policy remains the
responsibility of `provision`/`preflight`; the restore receipt does not claim to re-create those
production identities in the disposable database.

## DATA and CONTROL database credential rotation

1. Generate two independent strong values without printing them. Register them as
   `PROTECTED_DB_PASSWORD_NEXT` and `PROTECTED_DB_CONTROL_PASSWORD_NEXT` in the protected
   Environment.
2. Dispatch `rotate-db`. The operation validates both current limited identities and their
   policies, changes both passwords in one admin transaction, validates both NEXT identities
   and policies, and verifies rejection of both old credentials.
3. On any post-mutation failure, the operation restores both old values in one transaction and
   validates both rollback connections. Treat `DB_ROTATION_ROLLBACK_FAILED` as an emergency;
   do not promote secrets.
4. After a `PASS` receipt, replace canonical `PROTECTED_DB_PASSWORD` and
   `PROTECTED_DB_CONTROL_PASSWORD` with the corresponding NEXT values using the GitHub UI or
   an approved stdin-based `gh secret set --env protected-retrieval` session. Do not put a value
   directly in a command line.
5. Delete both NEXT Environment secrets.
6. Dispatch `preflight` and retain its non-sensitive result with the rotation receipt.
7. Remove the temporary runner `/32` rule immediately after the job.

The workflow never promotes or deletes GitHub Environment secrets itself. A failed run before
mutation needs no database rollback; a failed run after mutation performs and validates the
database rollback before returning a fixed error code.

## EC2 SSH key rotation

1. Generate a new keypair locally with `ssh-keygen`; do not record either raw key body in
   evidence.
2. While the old key remains authorized, append the new public key to the correct account's
   `authorized_keys` using the existing pinned-host SSH boundary.
3. Open a separate connection with the new private key and pinned `known_hosts`; require `PASS`
   before continuing.
4. Replace the canonical protected Environment `EC2_SSH_KEY` using the GitHub UI or stdin-based
   `gh secret set --env protected-retrieval`. Do not remove the old public key first.
5. Dispatch `preflight` and confirm the canonical workflow connects successfully with the new
   secret.
6. Only after both new-key checks pass, remove the old public key from `authorized_keys`.
7. Attempt a connection with the old private key and record rejection.
8. Record a non-sensitive receipt containing operation/result/UTC plus old and new public-key
   fingerprints only. Never record private/public key bodies.

If either new-key connection fails before old-key removal, restore the new public-key edit or
the canonical secret as appropriate while the old key still works. If the canonical workflow
fails, do not remove the old public key.

## Public evidence allowlists

Backup receipt fields are exactly `operation`, `result`, `backup_id`, `recorded_at`, `reason`,
`scope_ref`, `encrypted_artifact_sha256`, `encryption_key_version`, `acl_policy_ref`, and
`relation_count`.

Restore receipt fields are exactly `operation`, `result`, `backup_id`, `isolated_target`,
`production_target_guard`, `alembic_head`, `relation_validation`, `audit_integrity`,
`acl_validation`, and `target_cleanup`.

Database rotation receipt fields are exactly `operation`, `result`, `data_new_validation`,
`control_new_validation`, `data_old_rejection`, `control_old_rejection`, `rollback_readiness`,
and `recorded_at`.

SSH rotation evidence may contain only operation/result/UTC, the two public-key fingerprints,
and the old-key rejection result. Never retain actual paths, endpoints, usernames unless an
approved evidence contract requires one, credentials, credential hashes, encryption nonce/tag,
protected row or artifact content, or HOLDOUT bodies.

`BACKUP_RESTORE_AND_ROTATION_EVIDENCE` remains uncleared until the post-merge operational
rehearsal and independent verification are complete.
