"""Runtime RBAC: resolves a role name against the Role Master table.

Everything role-shaped goes through here — which screens a role may open, how
senior it is, whether one role may hand out another, and which built-in a
custom role behaves as. The table (accounts.Role) is seeded from the ROLE_ALLOW
/ ROLE_RANK constants and then owned by the property, so an edit in Role
Mapping or a role the property invented itself both take effect immediately.

The constants stay as the fallback for a database whose Role table isn't
populated yet (a migration mid-flight, a fixture-loaded test DB), which keeps
this module safe to call from anywhere.
"""
from django.core.cache import cache
from django.db import DatabaseError

from .constants import ROLE_ALLOW, ROLE_GM, ROLE_MD, ROLE_RANK, ROLE_SUPER_ADMIN

PROTECTED = {ROLE_SUPER_ADMIN, ROLE_MD, ROLE_GM}  # always "*", never editable

# RBAC is resolved on nearly every request, so the whole (small) table is
# cached as one entry. Writes bump the version key in models.py rather than
# expiring keys, so a stale entry can never outlive a permission change.
_CACHE_TTL = 300


def _rows():
    """Every Role row as {name: {base, rank, modules, active, is_system}}.

    Empty when the table doesn't exist or is unseeded — every caller below
    reads that as "fall back to the constants".
    """
    from .models import ROLE_CACHE_VERSION_KEY, Role
    version = cache.get(ROLE_CACHE_VERSION_KEY) or 1
    key = f"hearth:roles:rows:{version}"
    rows = cache.get(key)
    if rows is None:
        try:
            rows = {
                r.name: {"base": r.behaves_as, "rank": r.rank, "modules": r.modules,
                         "active": r.active, "is_system": r.is_system}
                for r in Role.objects.all()
            }
        except DatabaseError:
            return {}       # pre-migration — deliberately not cached
        cache.set(key, rows, _CACHE_TTL)
    return rows


def base_role(role):
    """The built-in whose rules this role plays by.

    Identity for the seventeen built-ins; for a custom role, whatever it was
    based on. Every cross-app check ("may this person approve a PO", "is this a
    Captain") runs its role through here first — which is what lets a property
    invent a role without touching a single approval chain.
    """
    row = _rows().get(role)
    return row["base"] if row else role


def role_rank(role):
    """Seniority, 0 for anything unknown.

    Retired roles still sitting on old accounts (a "Night Auditor" from before
    that role was folded into Front Office) rank lowest rather than highest — so
    whoever runs Users & Roles can still move that person onto a current role.
    They can never be handed OUT again; can_assign_role checks that.
    """
    row = _rows().get(role)
    if row is not None:
        return row["rank"]
    return ROLE_RANK.get(role, 0)


def can_assign_role(actor_role, target_role):
    """May `actor_role` create a user with `target_role` (or move one to it)?

    One rule: you may grant any role ranked at or below your own, never above.
    Inactive and unknown roles are grantable by nobody.
    """
    rows = _rows()
    if rows:
        target = rows.get(target_role)
        if target is None or not target["active"]:
            return False
        return target["rank"] <= role_rank(actor_role)
    if target_role not in ROLE_RANK:
        return False
    return ROLE_RANK[target_role] <= role_rank(actor_role)


def assignable_roles(actor_role):
    """Every role `actor_role` may hand out, most senior first — the source of
    truth behind the Users screen's role picker."""
    rows = _rows()
    order = (sorted(rows, key=lambda n: (-rows[n]["rank"], n)) if rows
             else sorted(ROLE_RANK, key=lambda n: (-ROLE_RANK[n], n)))
    return [n for n in order if can_assign_role(actor_role, n)]


def acting_role(request):
    """The built-in role whose rules apply to whoever made this request.

    Use this for behaviour checks (may they approve, may they take cash);
    use the raw `user.role` with can_access/role_can_access for screen access,
    since that reads the role's OWN mapping rather than its base's.
    """
    return base_role(getattr(getattr(request, "user", None), "role", "") or "")


def role_names_for(builtin):
    """Every role name that behaves as `builtin`, itself included.

    For the queries that go looking for people rather than permissions — the
    housekeeping attendant picker, the captain list — so a property's own
    "Floor Supervisor" based on Housekeeping turns up alongside the built-in.
    """
    rows = _rows()
    if not rows:
        return [builtin]
    return sorted({builtin} | {n for n, r in rows.items() if r["base"] == builtin})


def allowed_modules_for(role):
    """Return "*" or a list of module keys the role may access."""
    if base_role(role) in PROTECTED:
        return "*"
    row = _rows().get(role)
    if row is not None:
        return "*" if row["modules"] == "*" else list(row["modules"])
    return ROLE_ALLOW.get(role, [])


def can_access(role, module):
    allow = allowed_modules_for(role)
    return allow == "*" or module in allow
