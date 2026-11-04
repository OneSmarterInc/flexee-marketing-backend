"""Storage for the reporting connector (Flexee family reporting contract v1).

Codes and tokens are stored only as SHA-256 hashes. A connection is one administrator's
grant to one client: an access token (an hour) and a rotating refresh token (30 days).
"""
from django.conf import settings
from django.db import models


class ReportingClient(models.Model):
    """An OAuth client: Claude's metadata document (cimd) or a registered one (dcr)."""

    KINDS = (("cimd", "cimd"), ("dcr", "dcr"))

    client_id_hash = models.CharField(max_length=64, unique=True)
    client_id = models.CharField(max_length=500)
    kind = models.CharField(max_length=4, choices=KINDS)
    name = models.CharField(max_length=200, null=True, blank=True)
    redirect_uris = models.JSONField(default=list)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class ReportingCode(models.Model):
    """A single-use authorization code, valid for ten minutes."""

    code_hash = models.CharField(max_length=64, unique=True)
    client = models.ForeignKey(ReportingClient, on_delete=models.CASCADE, related_name="codes")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    redirect_uri = models.CharField(max_length=500)
    code_challenge = models.CharField(max_length=128)
    scope = models.CharField(max_length=64)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ReportingConnection(models.Model):
    """One administrator's connection: an access token and a rotating refresh token."""

    client = models.ForeignKey(ReportingClient, on_delete=models.CASCADE, related_name="connections")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="reporting_connections")
    scope = models.CharField(max_length=64)
    access_hash = models.CharField(max_length=64, db_index=True)
    access_expires_at = models.DateTimeField()
    refresh_hash = models.CharField(max_length=64, db_index=True)
    refresh_expires_at = models.DateTimeField()
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class ReportingCall(models.Model):
    """One tool call: which tool, how it went, how many rows. Never the rows."""

    connection = models.ForeignKey(ReportingConnection, on_delete=models.CASCADE,
                                   related_name="calls")
    tool = models.CharField(max_length=64)
    status = models.CharField(max_length=16)
    rows = models.PositiveIntegerField(default=0)
    ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
