---
id: manifest/01-manifest
module: manifest
title: "Manifest"
pages: null
---

# Manifest

_Manual pages not recorded_

# manifest.json

```json
{
  "kb_name": "Sample Knowledge Base",
  "kb_description": "A combined KB with HR policies and file type validation samples",
  "version": "1.0.0",
  "created_date": "2026-10-13",
  "file_count": 7,
  "sub_knowledge_bases": [
    {
      "name": "HR Policies",
      "files": [
        "user_manual.docx",
        "leave_policy.pdf"
      ]
    },
    {
      "name": "File Type Validation",
      "files": [
        "performance_metrics.csv",
        "quarterly_report.xlsx",
        "manifest.json",
        "config.xml",
        "requirements.txt"
      ]
    }
  ],
  "parser_engine": "mcp_ocr",
  "owner": "kb-admin"
}
```

- **kb_name**: Sample Knowledge Base
- **kb_description**: A combined KB with HR policies and file type validation samples
- **version**: 1.0.0
- **created_date**: 2026-10-13
- **file_count**: 7
- **sub_knowledge_bases**: [{"name": "HR Policies", "files": ["user_manual.docx", "leave_policy.pdf"]}, {"name": "File Type Validation", "files": ["performance_metrics.csv", "quarterly_report.xlsx", "manifest.json", "config.xml", "requirements.txt"]}]
- **parser_engine**: mcp_ocr
- **owner**: kb-admin
