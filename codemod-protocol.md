# Silver-Platter Codemod Protocol v1

This document describes how to write codemods (automated code modification scripts) that work with Silver-Platter.

## Overview

Codemods are commands that Silver-Platter runs in version control checkouts to make automated changes. Your codemod can be written in any language and should:

1. Make changes to files in the working directory
2. Optionally commit those changes (or let Silver-Platter handle it)
3. Exit with appropriate status codes
4. Optionally write metadata about the changes

## How Codemods Are Executed

Silver-Platter runs your codemod in a clean VCS checkout. You can:

- **Make and commit changes yourself** - Full control over the commit process
- **Just make changes** - Silver-Platter will auto-commit with a reasonable message
- **Make no changes** - Exit successfully to indicate no changes were needed

By default, uncommitted changes are discarded (with a warning). Use `--autocommit` to have Silver-Platter commit them automatically.

## Configuration Options

These can be specified via command-line flags or in a recipe file:

| Option | Description | Default |
|--------|-------------|---------|
| `name` | Codemod identifier | Filename |
| `command` | Command to execute | Required |
| `commit-message` | Template for commit messages (Jinja2) | Auto-generated |
| `description` | Merge proposal description (Jinja2, markdown/plain) | Auto-generated |
| `resume` | Whether the command supports resuming | `false` |
| `mode` | How to publish changes: `push`, `attempt-push`, `propose` | `attempt-push` |
| `propose-threshold` | Minimum change value before creating proposals | None |
| `autocommit` | Auto-commit uncommitted changes | `true` |
| `target-branch-url` | Override target branch URL | Base URL |

## Exit Codes

- **0**: Success (changes made or no changes needed)
- **1**: Failure (branch will be discarded)
- **Other**: Treated as failure

## Resuming Previous Runs

If your codemod supports resuming (set `resume: true` in config):

1. Silver-Platter may provide a previous branch to continue from
2. The `SVP_RESUME` environment variable will point to a JSON file with metadata from the last run
3. Your codemod should read this metadata and continue where it left off
4. Carry forward any relevant context from the previous run

If resuming is not supported, previous changes are discarded and may be recreated.

## Environment Variables

### Always Set

| Variable | Description | Example |
|----------|-------------|---------||
| `SVP_API` | Silver-Platter API version | `1` |
| `SVP_RESULT` | Path where your codemod should write result JSON | `/tmp/svp-result.json` |

### Conditionally Set

| Variable | Description | When Set |
|----------|-------------|----------|
| `COMMITTER` | Git committer identity | If configured |
| `SVP_RESUME` | Path to previous run's result JSON | If resuming and available |

## Result JSON Format

Write a JSON file to the path specified in `SVP_RESULT` with these fields:

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `code` | string | Result code (see below) |

### Result Codes

- `success` - Changes were successfully made
- `nothing-to-do` - No changes were needed
- Other values indicate specific error types

### Optional Fields

| Field | Type | Description |
|-------|------|-------------|
| `transient` | boolean | Whether the error is temporary (e.g., network issue) |
| `stage` | array | Stage names where the codemod failed |
| `description` | string | One-line description of changes or error |
| `value` | integer | Relative importance of changes (for prioritization) |
| `tags` | array | Tags to apply to the change |
| `context` | object | Custom data for template expansion |
| `target-branch-url` | string | Override target branch URL |

### Example Result JSON

```json
{
  "code": "success",
  "description": "Updated 5 deprecated API calls",
  "value": 50,
  "tags": ["api-migration", "automated"],
  "context": {
    "files_changed": 5,
    "apis_updated": ["oldAPI", "legacyAPI"]
  }
}
```

## Debian-Specific Operations

For Debian packages, additional features are available:

### Branch Naming

Branches follow [DEP-14](https://dep-team.pages.debian.net/deps/dep14/) conventions.

### Additional Environment Variables

| Variable | Description | Values |
|----------|-------------|--------|
| `DEB_SOURCE` | Source package name | e.g., `nginx` |
| `DEB_UPDATE_CHANGELOG` | Whether to update debian/changelog | `update`/`leave` |
| `ALLOW_REFORMATTING` | Whether reformatting is allowed | `true`/`false` |

## Complete Example

Here's a simple codemod that updates deprecated function calls:

```bash
#!/bin/bash
# update-deprecated-api.sh

# Check if we should resume
if [ -n "$SVP_RESUME" ] && [ -f "$SVP_RESUME" ]; then
    echo "Resuming from previous run..."
    # Load previous state
    PROCESSED_FILES=$(jq -r '.context.processed_files[]' "$SVP_RESUME" 2>/dev/null || echo "")
fi

# Make changes
CHANGED_COUNT=0
for file in $(find . -name "*.py" -type f); do
    if grep -q "old_function" "$file"; then
        sed -i 's/old_function/new_function/g' "$file"
        ((CHANGED_COUNT++))
    fi
done

# Write result
if [ $CHANGED_COUNT -gt 0 ]; then
    cat > "$SVP_RESULT" <<EOF
{
  "code": "success",
  "description": "Updated $CHANGED_COUNT files to use new API",
  "value": $((CHANGED_COUNT * 10)),
  "context": {
    "files_changed": $CHANGED_COUNT
  }
}
EOF
    git add -A
    git commit -m "Replace old_function with new_function

This updates deprecated API calls to use the new function name.
Affected files: $CHANGED_COUNT"
    exit 0
else
    cat > "$SVP_RESULT" <<EOF
{
  "code": "nothing-to-do",
  "description": "No deprecated API calls found"
}
EOF
    exit 0
fi
```
