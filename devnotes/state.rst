Keeping State
=============

We should keep state about each package/upstream and what merge
proposals we have created.

Keeping state is necessary so that we:
 * can display status on the web page
 * know what merge proposals to update, e.g. because:
  + upstream changes (i.e. the merge proposal is now conflicted)
  + the merge proposal is now merged
  + the merge proposal needs human attention (i.e. somebody commented)
  + there are new supported lintian tags in lintian-brush

Branch
 URL(s?) : string+
 Merge proposals*
  name : string
  last updated : time
  status : enum{"open", "conflicted", "merged", "closed", "needs-human-attention"}
  command : string

