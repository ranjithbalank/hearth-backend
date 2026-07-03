"""Runtime RBAC that honours editable RoleConfig overrides (falls back to the
built-in ROLE_ALLOW constants). Super Admin/MD/GM are always full-access."""
from .constants import ROLE_ALLOW, ROLE_GM, ROLE_MD, ROLE_SUPER_ADMIN

PROTECTED = {ROLE_SUPER_ADMIN, ROLE_MD, ROLE_GM}  # always "*", never editable


def allowed_modules_for(role):
    """Return "*" or a list of module keys the role may access."""
    if role in PROTECTED:
        return "*"
    from .models import RoleConfig
    cfg = RoleConfig.objects.filter(role=role).first()
    if cfg is not None:
        return cfg.modules
    return ROLE_ALLOW.get(role, [])


def can_access(role, module):
    allow = allowed_modules_for(role)
    return allow == "*" or module in allow
