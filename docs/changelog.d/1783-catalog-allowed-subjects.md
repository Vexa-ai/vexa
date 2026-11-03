- **Model catalog: a model open to named people only (#1783).** A catalog entry may carry
  `allowed_subjects`, a list of sign-in addresses or subject ids. Only those people see the model in
  the picker. Anyone else who names it, by a pick or by a stored chat pick, gets the typed
  `not_permitted` refusal before anything starts. Use it for a model on a credential that belongs to
  one person, such as a personal Claude subscription.
  A meeting's agent worker is one per person per meeting session (its workload id now ends in the
  subject), so each person's turns run on a worker started with their own model route.
