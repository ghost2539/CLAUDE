from __future__ import annotations


def search_for_user(session_data, identifiers, db_session, local_search_one):
    """EBS query for AD and LOCAL users; public credentials as fallback."""
    import integracoes.ebs_service as ebs_service
    if session_data.get("auth_source") == "AD" and session_data.get("ebs_auth"):
        auth = session_data["ebs_auth"]
    else:
        from routers.public_assets import _auth
        auth = _auth()
    try:
        return ebs_service.search_many(auth, identifiers)
    except Exception:
        if session_data.get("auth_source") != "AD":
            from routers.public_assets import _auth
            return ebs_service.search_many(_auth(True), identifiers)
        raise
