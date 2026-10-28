- **`flows_submit` takes the shape agents send (#1783).** The tool now publishes `steps` as a list of
  step names, so an agent no longer sends objects and gets `422`. A person may name their own scope
  by sign-in address (`user:<address>`) as well as by id; it is stored as `user:<id>`, and anyone
  else's scope is still refused. See [Routines](/flows/routines#how-it-works).
