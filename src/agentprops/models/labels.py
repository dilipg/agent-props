"""The label vocabulary a blueprint declares, and the values a dataset carries.

`docs/prd.md` section 5.3. Labels are typed, not free-form: the blueprint owns
the dimensions and their permitted values, and every dataset carries one value
for every declared dimension. Partial labelling is what makes ``dataset_find``
unreliable, so DS-024 rejects it - but the *rule* rejects it, not this model
(ruling R-04), which is why :data:`Labels` is a plain mapping with no required
keys.
"""

from agentprops.models.base import StrictModel

__all__ = ["LabelSchema", "Labels"]

#: A dataset's labels: one value per dimension. DS-012 checks each dimension
#: and value against the blueprint's vocabulary; DS-024 checks completeness.
#: DS-013 ("no dimension appears twice") is structurally unreachable through a
#: JSON document and therefore unrepresentable here - see ruling R-14.
type Labels = dict[str, str]


class LabelSchema(StrictModel):
    """A blueprint's label vocabulary: dimension name to permitted values.

    BP-013 checks that every dimension has at least one value and that values
    within a dimension are unique.
    """

    dimensions: dict[str, list[str]]
