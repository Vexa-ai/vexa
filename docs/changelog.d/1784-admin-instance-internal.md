- **`GET /admin/instance` is gone (#1784).** Whether the instance has an administrator yet is now read
  on admin-api's internal tier only, at `GET /internal/instance` with `X-Internal-Secret`; nothing in
  the product called the admin route. `/internal/settings/global_setup` answers `404`: the
  company-layer gate it stored was retired in #1783 and nothing read it.
