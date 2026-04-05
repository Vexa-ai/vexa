# Code — agents already know how to work with this

```
src/
├── models/
│   ├── user.py            # schema, relationships, validation
│   ├── company.py         # references user.py, product.py
│   └── product.py         # pricing, features, integrations
│
├── services/
│   ├── email.py           # send(to, subject, body)
│   ├── reporting.py       # generate charts, export PDF
│   ├── scheduler.py       # cron jobs, reminders
│   └── notifications.py   # slack, webhook, push
│
└── README.md
```

# Knowledge — same structure, different domain

```
knowledge/
├── entities/
│   ├── contacts/          # people — schema, relationships, context
│   ├── companies/         # references contacts, products
│   └── products/          # competitors, integrations, features
│
├── outputs/
│   ├── emails/            # draft(to, subject, body)
│   ├── calm-charts/       # architecture visualizations
│   ├── meeting-minutes/   # structured summaries
│   └── action-items/      # owner, due date, blockers
│
└── README.md
```

Same shape. Same operations. `read`, `write`, `execute`.
The agent doesn't need new skills — just new data.
