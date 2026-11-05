"""Rate limits on the endpoints that take a password or create an account (audit M4).

The rate is read from settings on every request, so tests can switch it on for themselves with
override_settings, and a site can change it with FLEXEE_AUTH_RATE without a code change.
"""
from django.conf import settings
from rest_framework.throttling import ScopedRateThrottle


class AuthRateThrottle(ScopedRateThrottle):
    scope_attr = "throttle_scope"

    def get_rate(self):
        return getattr(settings, "FLEXEE_AUTH_RATE", None) or None   # None = no limit

    def allow_request(self, request, view):
        self.rate = self.get_rate()
        if self.rate is None:
            return True
        self.num_requests, self.duration = self.parse_rate(self.rate)
        self.scope = "auth"
        self.key = self.get_cache_key(request, view)
        if self.key is None:
            return True
        self.history = self.cache.get(self.key, [])
        self.now = self.timer()
        while self.history and self.history[-1] <= self.now - self.duration:
            self.history.pop()
        if len(self.history) >= self.num_requests:
            return self.throttle_failure()
        return self.throttle_success()
