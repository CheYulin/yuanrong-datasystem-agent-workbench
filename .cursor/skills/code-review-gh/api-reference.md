# GitCode API Reference for Code Review

## Authentication

All API calls require Bearer Token authentication:
```bash
curl -H "Authorization: Bearer $GITCODE_TOKEN" "https://api.gitcode.com/api/v5/..."
```

## Key Endpoints

### Get PR Details
```bash
curl -s -H "Authorization: Bearer $GITCODE_TOKEN" \
  "https://api.gitcode.com/api/v5/repos/openeuler/yuanrong-datasystem/pulls/{number}"
```

Response includes: `number`, `title`, `state`, `body`, `author`, `head/base ref`, `mergeable`, `labels`, etc.

### Get PR Changed Files
```bash
curl -s -H "Authorization: Bearer $GITCODE_TOKEN" \
  "https://api.gitcode.com/api/v5/repos/openeuler/yuanrong-datasystem/pulls/{number}/files"
```

Response includes array of files with:
- `filename`, `status`, `additions`, `deletions`
- `patch.diff` - the actual diff
- `sha` - commit SHA

### Get PR Comments
```bash
curl -s -H "Authorization: Bearer $GITCODE_TOKEN" \
  "https://api.gitcode.com/api/v5/repos/openeuler/yuanrong-datasystem/pulls/{number}/comments"
```

### Post Comment
```bash
curl -s -X POST -H "Authorization: Bearer $GITCODE_TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{"body": "Comment text", "path": "file.cpp", "position": 123}' \
  "https://api.gitcode.com/api/v5/repos/openeuler/yuanrong-datasystem/pulls/{number}/comments"
```

## Response Format

```json
{
  "id": "comment_id",
  "body": "Comment content",
  "path": "src/file.cpp",       // for line comments
  "position": 123,               // line number in diff
  "new_position": 123,           // alternative line reference
  "discussion_id": "thread_id",
  "created_at": "2026-05-01T00:00:00+08:00",
  "user": {
    "login": "reviewer_username",
    "html_url": "https://gitcode.com/reviewer"
  }
}
```

## Understanding Diff Position

The `position` parameter in GitCode API:
- **For additions**: 1-indexed line number in the "new" file (after the `+` is applied)
- **GitCode quirk**: May need to be the exact line in the diff output, not the source file

### Finding Correct Position

When posting to a specific line:
1. Get the diff: `GET /pulls/{number}/files`
2. Find the file in the `patch.diff` output
3. Count lines from the start of the file's diff hunk
4. Use that count as `position`

## Comment Types

| Type | Parameters | Description |
|------|------------|-------------|
| General | `body` only | PR-level comment |
| Line | `body`, `path`, `position` | Comment on specific line |
| Reply | `body`, `in_reply_to_id` | Reply to existing comment |

## Best Practices

### Posting Multiple Comments
```bash
# Add delay between posts
sleep 1

# Batch similar comments together
COMMENTS=(
  '{"body": "[Minor] Comment 1", "path": "file1.cpp", "position": 10}'
  '{"body": "[Minor] Comment 2", "path": "file2.cpp", "position": 20}'
)

for c in "${COMMENTS[@]}"; do
  curl -s -X POST -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$c" \
    "https://api.gitcode.com/api/v5/repos/openeuler/yuanrong-datasystem/pulls/${PR}/comments"
  sleep 1
done
```

### Markdown Support
GitCode comments support Markdown:
```markdown
## Code Review

**[Critical]** This will break production if X happens

**建议**: Consider using Y instead

- Item 1
- Item 2

\`\`\`
code block
\`\`\`
```

## Error Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 400 | Bad request - check JSON format |
| 401 | Unauthorized - check token |
| 403 | Forbidden - token lacks permissions |
| 404 | PR/file not found |