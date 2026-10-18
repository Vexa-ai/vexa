- **Service images are named `vexaai/vexa-<service>` (#1783).** The ten service images were
  `vexaai/v012-<service>`. v0.13.3 is published under both names with the same digest; from v0.13.4
  only the new names are published. Compose and the Helm chart now default to the release tag rather
  than the moving `v012` tag. On upgrade set `IMAGE_TAG` (Compose) or `global.imageTag` (Helm) to
  `v0.13.3`, rename any image overrides, and add the new repositories to a registry mirror. See
  [Image names from v0.13.3](/upgrade-image-names).
