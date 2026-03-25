from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True
    dependencies = []

    operations = [

        # ── PublishLog ────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="PublishLog",
            fields=[
                ("id",             models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("username",       models.CharField(db_index=True, max_length=255)),
                ("source_repo",    models.URLField(blank=True, max_length=1000)),
                ("source_file",    models.CharField(max_length=500)),
                ("target_wiki",    models.CharField(max_length=255)),
                ("target_page",    models.CharField(max_length=500)),
                ("version",        models.CharField(blank=True, max_length=100)),
                ("edit_summary",   models.TextField(blank=True)),
                ("status",         models.CharField(
                    choices=[("success","Success"),("failed","Failed"),("pending_review","Pending Review")],
                    db_index=True, default="pending_review", max_length=30,
                )),
                ("publish_method", models.CharField(
                    choices=[("botpassword","BotPassword"),("draft","Draft / Notification only")],
                    default="draft", max_length=30,
                )),
                ("error_message",  models.TextField(blank=True)),
                ("created_at",     models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={"ordering": ["-created_at"], "verbose_name": "Publish Log Entry", "verbose_name_plural": "Publish Log Entries"},
        ),

        # ── TrackedScript ─────────────────────────────────────────────────────
        migrations.CreateModel(
            name="TrackedScript",
            fields=[
                ("id",              models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("username",        models.CharField(db_index=True, max_length=255)),
                ("source_repo",     models.URLField(max_length=1000)),
                ("source_file",     models.CharField(max_length=500)),
                ("target_wiki",     models.CharField(max_length=255)),
                ("target_page",     models.CharField(max_length=500)),
                ("last_hash",       models.CharField(blank=True, default="", max_length=64)),
                ("last_checked_at", models.DateTimeField(blank=True, null=True)),
                ("has_update",      models.BooleanField(default=False)),
                ("is_active",       models.BooleanField(default=True)),
                ("created_at",      models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at"], "verbose_name": "Tracked Script"},
        ),
        migrations.AddConstraint(
            model_name="trackedscript",
            constraint=models.UniqueConstraint(
                fields=["username", "source_repo", "source_file", "target_wiki", "target_page"],
                name="unique_tracked_script",
            ),
        ),

        # ── UserPreference ────────────────────────────────────────────────────
        migrations.CreateModel(
            name="UserPreference",
            fields=[
                ("username",     models.CharField(max_length=255, primary_key=True, serialize=False)),
                ("opted_out",    models.BooleanField(default=False)),
                ("opted_in_at",  models.DateTimeField(auto_now_add=True)),
                ("opted_out_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"verbose_name": "User Preference"},
        ),

        # ── NotificationLog ───────────────────────────────────────────────────
        migrations.CreateModel(
            name="NotificationLog",
            fields=[
                ("id",                models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("tracked_script",    models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="notifications",
                    to="publisher.trackedscript",
                )),
                ("notified_username", models.CharField(max_length=255)),
                ("notified_wiki",     models.CharField(max_length=255)),
                ("talk_page",         models.CharField(max_length=500)),
                ("success",           models.BooleanField(default=False)),
                ("revision_id",       models.IntegerField(blank=True, null=True)),
                ("error_message",     models.TextField(blank=True)),
                ("sent_at",           models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={"ordering": ["-sent_at"], "verbose_name": "Notification Log"},
        ),
    ]
