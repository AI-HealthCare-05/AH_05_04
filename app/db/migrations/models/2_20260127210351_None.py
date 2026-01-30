from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS `aerich` (
    `id` INT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `version` VARCHAR(255) NOT NULL,
    `app` VARCHAR(100) NOT NULL,
    `content` JSON NOT NULL
) CHARACTER SET utf8mb4;
CREATE TABLE IF NOT EXISTS `users` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `email` VARCHAR(40) NOT NULL,
    `hashed_password` VARCHAR(128) NOT NULL,
    `name` VARCHAR(20) NOT NULL,
    `gender` VARCHAR(6) NOT NULL COMMENT 'MALE: MALE\nFEMALE: FEMALE',
    `birthday` DATE NOT NULL,
    `phone_number` VARCHAR(11) NOT NULL,
    `is_active` BOOL NOT NULL DEFAULT 1,
    `is_admin` BOOL NOT NULL DEFAULT 0,
    `last_login` DATETIME(6),
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)
) CHARACTER SET utf8mb4;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """


MODELS_STATE = (
    "eJztmFtvmzAUgP9KxNMmdVVCrutbaNM1U5NMbbpN7SrkgEOsgk2xaRtV/e+zDQl3SqpuSa"
    "a9JHAucM7nwzmGZ8UhJrTpYR96yFgoR7VnBQMH8oOU5qCmANeN5ELAwMyWpiCymVHmAYNx"
    "6RzYFHKRCanhIZchgrkU+7YthMTghghbkcjH6N6HOiMWZAvoccXNLRcjbMInSFen7p0+R9"
    "A2E6EiU9xbynW2dKVsiNmpNBR3m+kGsX0HR8buki0IXlsjzITUghh6gEFxeeb5InwRXZjn"
    "KqMg0sgkCDHmY8I58G0WS7ciA4NgwY9HQ2WClrjLJ7XR6rZ6zU6rx01kJGtJ9yVIL8o9cJ"
    "QExlPlReoBA4GFxBhxe4AeFSFl4B0vgJdPL+aSQsgDTyNcAStjuBJEEKPCeSeKDnjSbYgt"
    "JgpcbbdLmH3vXxyf9S8+cKuPIhvCizmo8XGoUgOdABuBFI/GBhBD8/0E2KjXKwDkVoUApS"
    "4JkN+RweAZTEL8ejkZ50OMuaRAXmGe4I2JDHZQsxFlt7uJtYSiyFoE7VB6b8fhfRj1f6a5"
    "Hp9PNEmBUGZ58iryAhpnLFrm/C728AvBDBh3j8Az9YyGqKTINqtyVCctARhYkpXIWOQXDp"
    "ErKht6ZrhIeelo8bkF3a3JoiHrHxoun1W12eyq9Wan1251u+1efT1lsqqycaMNv4iJk6jN"
    "10cQdACyN+mda4f97J6tKs2zVdw7W5nWuQB0AU3dBZQ+Ei+nXotZ5rjuJ9WG2qsyk9Re8U"
    "wSuiRY+b8BzZX9fiJUqxSmWlyYaqYwecZm0N6zBAfYdyTFIQ8JYANmaEbeW+apjPrng6Oa"
    "+P2FTwfBWfCvvIFzpwLmTiHlThryDHlsYYJlFvMJh5NfqHGfFFzepyFDDjwUB7tZtiX8Tv"
    "rTQYqPy7ODOq+2WVEp5jNK++3nQ91oVGmLjeKu2EjXG6I634Shh5zOqBFiQ4ALNkZxvxTM"
    "GXf8UzTXm6b3rjVtMjlPbNG1YWrzM74aaQOOV9LlRogl9kRJpqaDct7DX0W6cvuLRDfdfW"
    "8FqQ0o021i5UE9CXtcPtWkZ1l7FAcVIIcVuBsdcjocDS6n/dG3BGfRN4VGldJlSpoZR+uL"
    "1H4Mp2c1cVq7nowH6ZfQtd30WhExAZ8RHZNHXrbxtFfilSj5YcCDAq0Ocr4NlC9k0vMdFn"
    "Ib3ZznYE6wvQzraE9WNiz50oX1XfONC5v0/L+wW11YGfxWvzK9/AYnG+YA"
)
