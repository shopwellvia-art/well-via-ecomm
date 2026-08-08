from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.models.rbac import Role
from app.models.user import User
from app.repositories.permission_repository import PermissionRepository
from app.repositories.role_repository import RoleRepository
from app.repositories.user_repository import UserRepository
from app.schemas.rbac import RoleCreate, RoleUpdate


class RoleService:
    def __init__(self, db: Session):
        self.db = db
        self.roles = RoleRepository(db)
        self.permissions = PermissionRepository(db)
        self.users = UserRepository(db)

    def list_all(self) -> list[Role]:
        return self.roles.list_all()

    def get(self, role_id: int) -> Role:
        role = self.roles.get_with_permissions(role_id)
        if not role:
            raise NotFoundError("Role not found")
        return role

    def _resolve_permissions(self, actor: User, permission_ids: list[int]) -> list:
        """Turn permission ids into rows, refusing any the actor does not hold.

        The same "no granting what you do not hold" rule `assign_to_user` guard
        #3 applies, enforced here because editing a role's CONTENTS is the other
        half of the same door. Without it the assignment guard is decorative: a
        scoped actor holding `roles.update` who belongs to role R could PATCH R
        to contain the whole registry, and `User.permissions` — which is the
        union over the actor's roles — would hand them every one of them on the
        next request, without a single role ASSIGNMENT taking place.

        `User.permissions` expands to the full registry for is_admin, so a
        superadmin passes trivially and a scoped actor can only ever pass on
        their own subset.
        """
        perms = [
            p for p in (self.permissions.get(pid) for pid in permission_ids)
            if p is not None
        ]
        escalation = sorted({p.name for p in perms} - set(actor.permissions))
        if escalation:
            raise ForbiddenError(
                "You cannot grant permissions you do not hold yourself: "
                + ", ".join(escalation[:5])
                + ("…" if len(escalation) > 5 else "")
            )
        return perms

    def create(self, actor: User, data: RoleCreate) -> Role:
        if self.roles.get_by_name(data.name):
            raise ConflictError(f"Role '{data.name}' already exists")
        role = Role(name=data.name, description=data.description, is_system=False)
        if data.permission_ids:
            role.permissions = self._resolve_permissions(actor, data.permission_ids)
        self.roles.add(role)
        self.db.commit()
        return self.get(role.id)

    def update(self, actor: User, role_id: int, data: RoleUpdate) -> Role:
        role = self.get(role_id)
        if data.name is not None and data.name != role.name:
            if role.is_system:
                raise ForbiddenError("System roles cannot be renamed")
            if self.roles.get_by_name(data.name):
                raise ConflictError(f"Role '{data.name}' already exists")
            role.name = data.name
        if data.description is not None:
            role.description = data.description
        if data.permission_ids is not None:
            role.permissions = self._resolve_permissions(actor, data.permission_ids)
        self.db.commit()
        return self.get(role_id)

    def delete(self, role_id: int) -> None:
        role = self.get(role_id)
        if role.is_system:
            raise ForbiddenError("System roles cannot be deleted")
        self.roles.delete(role)
        self.db.commit()

    def assign_to_user(self, actor: User, user_id: int, role_ids: list[int]) -> User:
        """Replace a user's role set.

        This is the most dangerous write in the admin — it is how admin access
        is granted — so it carries four guards. Without them, `users.assign_role`
        alone is enough to escalate to a full takeover.
        """
        user = self.users.get(user_id)
        if not user:
            raise NotFoundError("User not found")

        # 1. Nobody edits their own access. Closes the direct self-escalation
        #    path (grant yourself `admin`, keep the session you already have).
        if actor.id == user.id:
            raise ForbiddenError(
                "You cannot change your own roles. Ask another admin."
            )

        # 2. Same superadmin shield the rest of user management uses. A
        #    non-superadmin holding users.assign_role must not be able to
        #    re-role an is_admin account out of (or into) its privileges.
        if user.is_admin and not actor.is_admin:
            raise ForbiddenError("Only a superadmin can modify a superadmin account")

        roles = [r for r in (self.roles.get(rid) for rid in role_ids) if r is not None]

        # 3. No granting what you do not hold. `User.permissions` expands to the
        #    whole registry for is_admin, so a superadmin passes trivially and a
        #    scoped actor can only ever pass on their own subset.
        actor_perms = set(actor.permissions)
        granting = {p.name for r in roles for p in r.permissions}
        escalation = sorted(granting - actor_perms)
        if escalation:
            raise ForbiddenError(
                "You cannot grant permissions you do not hold yourself: "
                + ", ".join(escalation[:5])
                + ("…" if len(escalation) > 5 else "")
            )

        # 4. Never strip the last active superadmin. is_admin is a column rather
        #    than a role so this only bites when a superadmin is *also* scoped by
        #    roles, but the check is cheap and the failure mode is a lockout.
        if (
            user.is_admin
            and user.is_active
            and self.users.count_active_superadmins(excluding_user_id=user.id) == 0
            and not roles
        ):
            raise ForbiddenError(
                "This is the last active superadmin — removing its roles would "
                "leave nobody able to administer the store."
            )

        user.roles = roles
        self.db.commit()
        self.db.refresh(user)
        return user
