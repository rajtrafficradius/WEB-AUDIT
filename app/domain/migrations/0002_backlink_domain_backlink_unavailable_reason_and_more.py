# ruff: noqa
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("domain", "0001_initial"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="backlink",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("availability", "unavailable"), _negated=True),
                    models.Q(("unavailable_reason", ""), _negated=True),
                    _connector="OR",
                ),
                name="domain_backlink_unavailable_reason",
            ),
        ),
        migrations.AddConstraint(
            model_name="connection",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("availability", "unavailable"), _negated=True),
                    models.Q(("unavailable_reason", ""), _negated=True),
                    _connector="OR",
                ),
                name="domain_connection_unavailable_reason",
            ),
        ),
        migrations.AddConstraint(
            model_name="evidence",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("availability", "unavailable"), _negated=True),
                    models.Q(("unavailable_reason", ""), _negated=True),
                    _connector="OR",
                ),
                name="domain_evidence_unavailable_reason",
            ),
        ),
        migrations.AddConstraint(
            model_name="keyword",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("availability", "unavailable"), _negated=True),
                    models.Q(("unavailable_reason", ""), _negated=True),
                    _connector="OR",
                ),
                name="domain_keyword_unavailable_reason",
            ),
        ),
        migrations.AddConstraint(
            model_name="metricobservation",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("availability", "unavailable"), _negated=True),
                    models.Q(("unavailable_reason", ""), _negated=True),
                    _connector="OR",
                ),
                name="domain_metric_unavailable_reason",
            ),
        ),
        migrations.AddConstraint(
            model_name="sourceimport",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("availability", "unavailable"), _negated=True),
                    models.Q(("unavailable_reason", ""), _negated=True),
                    _connector="OR",
                ),
                name="domain_sourceimport_unavailable_reason",
            ),
        ),
        migrations.AddConstraint(
            model_name="sourcesnapshot",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("availability", "unavailable"), _negated=True),
                    models.Q(("unavailable_reason", ""), _negated=True),
                    _connector="OR",
                ),
                name="domain_sourcesnapshot_unavailable_reason",
            ),
        ),
    ]
