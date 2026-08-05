"""Sign-in that doesn't care about capitals.

Django's default backend looks the username up exactly, so `Abishek1828` and
`abishek1828` are different logins. New accounts are normalised to lower case
(validators.validate_username), but accounts created before that — and anyone
typing their own name with a capital at the sign-in box — still need to land on
the right record.

Exact match wins first. Only if nothing matches exactly does this fall back to
a case-insensitive lookup, and only when that lookup is unambiguous: a database
that already contains both spellings keeps behaving exactly as it did, rather
than this quietly picking one of the two for someone.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class CaseInsensitiveUsernameBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        if username is None or password is None:
            return None

        user = User.objects.filter(username=username).first()
        if user is None:
            matches = list(User.objects.filter(username__iexact=username)[:2])
            if len(matches) != 1:
                # Zero matches, or a legacy pair differing only in case: run the
                # password hasher anyway so a wrong username can't be told from
                # a wrong password by how long the response took.
                User().set_password(password)
                return None
            user = matches[0]

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
