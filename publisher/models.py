from django.db import models


class PublishLog(models.Model):
    """
    Audit record of every publish action attempted through WikiScriptSync.
    Every deployment is recorded — who did it, what changed, where it went.
    """

    STATUS_CHOICES = [
        ("success", "Success"),
        ("failed", "Failed"),
        ("pending_review", "Pending Review"),
    ]
    PUBLISH_METHOD_CHOICES = [
        ("botpassword", "BotPassword"),
        ("draft", "Draft / Notification only"),
    ]

    username       = models.CharField(max_length=255, db_index=True)
    source_repo    = models.URLField(max_length=1000, blank=True)
    source_file    = models.CharField(max_length=500)
    target_wiki    = models.CharField(max_length=255)
    target_page    = models.CharField(max_length=500)
    version        = models.CharField(max_length=100, blank=True)
    edit_summary   = models.TextField(blank=True)
    status         = models.CharField(
        max_length=30, choices=STATUS_CHOICES,
        default="pending_review", db_index=True,
    )
    publish_method = models.CharField(
        max_length=30, choices=PUBLISH_METHOD_CHOICES, default="draft",
    )
    error_message  = models.TextField(blank=True)
    created_at     = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Publish Log Entry"
        verbose_name_plural = "Publish Log Entries"

    def __str__(self):
        return f"{self.username} → {self.target_page} ({self.status})"


class TrackedScript(models.Model):
    """
    A source file being watched for upstream changes.

    Created when a user publishes via the tool. The notifier checks all
    active TrackedScript rows and posts a talk-page notification if the
    source file hash has changed since the last check.
    """

    username        = models.CharField(max_length=255, db_index=True)
    source_repo     = models.URLField(max_length=1000)
    source_file     = models.CharField(max_length=500)
    target_wiki     = models.CharField(max_length=255)
    target_page     = models.CharField(max_length=500)

    # SHA-256 hash of the file content at last check. Empty = never checked.
    last_hash       = models.CharField(max_length=64, blank=True, default="")
    last_checked_at = models.DateTimeField(null=True, blank=True)
    has_update      = models.BooleanField(default=False)
    is_active       = models.BooleanField(default=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["username", "source_repo", "source_file", "target_wiki", "target_page"],
                name="unique_tracked_script",
            )
        ]
        verbose_name = "Tracked Script"

    def __str__(self):
        return f"{self.source_file} → {self.target_wiki}/{self.target_page}"


class UserPreference(models.Model):
    """
    Opt-in / opt-out preferences for talk-page notifications.
    One row per Wikimedia username.
    """

    username     = models.CharField(max_length=255, primary_key=True)
    opted_out    = models.BooleanField(default=False)
    opted_in_at  = models.DateTimeField(auto_now_add=True)
    opted_out_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "User Preference"

    def __str__(self):
        return f"{self.username} ({'opted out' if self.opted_out else 'subscribed'})"


class NotificationLog(models.Model):
    """
    Record of every talk-page notification attempt.
    Allows admins to see what was sent, when, and whether it succeeded.
    """

    tracked_script    = models.ForeignKey(
        TrackedScript, on_delete=models.CASCADE, related_name="notifications"
    )
    notified_username = models.CharField(max_length=255)
    notified_wiki     = models.CharField(max_length=255)
    talk_page         = models.CharField(max_length=500)
    success           = models.BooleanField(default=False)
    revision_id       = models.IntegerField(null=True, blank=True)
    error_message     = models.TextField(blank=True)
    sent_at           = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-sent_at"]
        verbose_name = "Notification Log"

    def __str__(self):
        status = "OK" if self.success else "FAIL"
        return f"[{status}] {self.notified_wiki}/User_talk:{self.notified_username}"
