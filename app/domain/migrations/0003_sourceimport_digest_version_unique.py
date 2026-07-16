from django.db import migrations, models
from django.db.models import Count


def reject_duplicate_import_identities(apps, schema_editor):
    source_import = apps.get_model("domain", "SourceImport")
    duplicate_exists = (
        source_import.objects.values("project", "source_type", "sha256", "schema_version")
        .annotate(identity_count=Count("pk"))
        .filter(identity_count__gt=1)
        .exists()
    )
    if duplicate_exists:
        raise RuntimeError(
            "Resolve duplicate SourceImport identities before applying the immutable constraint."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("domain", "0002_backlink_domain_backlink_unavailable_reason_and_more"),
    ]

    operations = [
        migrations.RunPython(reject_duplicate_import_identities, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="sourceimport",
            constraint=models.UniqueConstraint(
                fields=("project", "source_type", "sha256", "schema_version"),
                name="domain_sourceimport_digest_version_unique",
            ),
        ),
    ]
