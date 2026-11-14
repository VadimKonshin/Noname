from django.db import migrations


def guard_unique_emails(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    seen = {}
    for pk, email in User.objects.exclude(email='').values_list('pk', 'email'):
        seen.setdefault(email.strip().lower(), []).append(pk)
    dupes = {email: pks for email, pks in seen.items() if len(pks) > 1}
    if dupes:
        raise RuntimeError(f'Одинаковые почты нужно развести до уникального индекса: {dupes}')


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0014_account_mail_and_brand_deletion'),
    ]

    operations = [
        migrations.RunPython(guard_unique_emails, migrations.RunPython.noop),
        migrations.RunSQL(
            sql=(
                'CREATE UNIQUE INDEX IF NOT EXISTS auth_user_email_ci '
                "ON auth_user (LOWER(email)) WHERE email <> '';"
            ),
            reverse_sql='DROP INDEX IF EXISTS auth_user_email_ci;',
        ),
    ]
