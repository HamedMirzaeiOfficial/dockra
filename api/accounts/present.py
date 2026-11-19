from config.i18n import tr

def user_payload(user, membership):
    full = (user.get_full_name() or user.username).strip()
    parts = full.split()
    if len(parts) >= 2:
        initials = parts[0][:1] + parts[1][:1]
    else:
        initials = full[:2]
    return {
        "id": user.id,
        "username": user.username,
        "name": full,
        "initials": initials,
        "role": membership.role,
        "role_label": tr(membership.get_role_display()),
        "organization": {
            "name": membership.organization.name,
            "slug": membership.organization.slug,
        },
    }
