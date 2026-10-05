"""
Helper for calling the iGOT user group search & create and CBP plan create & publish APIs.
"""
from datetime import date
from typing import List, Optional

import httpx
from fastapi import HTTPException

from ..core.configs import settings
from ..core.logger import logger


def extract_content_list(cbp_plan_data_list: list) -> List[dict]:
    seen: set = set()
    content_list: List[dict] = []
    records = cbp_plan_data_list if isinstance(cbp_plan_data_list, list) else [cbp_plan_data_list]
    for record in records:
        for course in record.get("selected_courses", []):
            identifier = course.get("identifier")
            if identifier and identifier not in seen:
                seen.add(identifier)
                content_list.append(
                    {"identifier": identifier, "mandatory": bool(course.get("mandatory", False))}
                )
    return content_list


def format_user_group_name(designation: str) -> str:
    """Trim and join words with underscores, upper-casing each word's first letter: 'assistant section officer' -> 'Assistant_Section_Officer'."""
    return "_".join(word[:1].upper() + word[1:].lower() for word in designation.strip().split())


async def call_igot_search_user_group(
    token: str,
    org_id: str,
    group_name: str,
    org: str = "dopt",
    rootorg: str = "igot",
) -> Optional[str]:
    """
    POST to iGOT user group searchV2 API. Returns the ID of the ACTIVE group with exactly this name in the org,
    or None if there is none (the API answers 404 when nothing matches).
    Raises HTTPException(502) on any other failure.
    """
    url = f"{settings.KB_BASE_URL}/api/usergroup/v1/searchV2"

    payload = {
        "request": {
            "filters": {
                "usergroupname": group_name,
                "orgId": org_id,
            },
            "limit": 10,
            "offset": 0,
        }
    }

    headers = {
        "Content-Type": "application/json",
        "accept": "application/json",
        "org": org,
        "rootorg": rootorg,
        "Authorization": f"{settings.KB_AUTH_TOKEN}",
        "x-authenticated-user-token": token,
        "x-authenticated-user-orgid": org_id,
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, json=payload, headers=headers, timeout=30.0)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"iGOT user group search API HTTP error: {e.response.status_code} | body={e.response.text}")
            raise HTTPException(
                status_code=502,
                detail=f"iGOT user group search API returned an error ({e.response.status_code}). Approval was not saved.",
            )
        except httpx.RequestError as e:
            logger.error(f"iGOT user group search API unreachable: {str(e)}")
            raise HTTPException(status_code=502, detail="iGOT user group search API is unreachable. Approval was not saved.")

    for group in (resp.json().get("result") or {}).get("content") or []:
        if str(group.get("status", "active")).lower() != "active":
            continue
        if group.get("usergroupname") == group_name and group.get("usergroupid"):
            return group["usergroupid"]

    logger.info(f"iGOT user group search found no ACTIVE group named '{group_name}' in org {org_id}")
    return None


async def get_or_create_user_group(token: str, org_id: str, designation: str) -> str:
    """
    Reuse the org's user group named after the designation, creating it only if the search finds none.
    Returns the user group ID. Raises HTTPException(502) on failure.
    """
    group_name = format_user_group_name(designation)

    user_group_id = await call_igot_search_user_group(token=token, org_id=org_id, group_name=group_name)
    if user_group_id:
        logger.info(f"Reusing iGOT user group '{group_name}' ({user_group_id}) in org {org_id}")
        return user_group_id

    user_group_id = await call_igot_create_user_group(
        token=token,
        org_id=org_id,
        group_name=group_name,
        designations=[designation],
    )
    logger.info(f"Created iGOT user group '{group_name}' ({user_group_id}) in org {org_id}")
    return user_group_id


async def call_igot_create_user_group(
    token: str,
    org_id: str,
    group_name: str,
    designations: List[str],
    org: str = "dopt",
    rootorg: str = "igot",
) -> str:
    """
    POST to iGOT user group create API. Returns the created user group ID.
    Raises HTTPException(502) on failure.
    """
    url = f"{settings.KB_BASE_URL}/api/usergroup/v1/create"

    payload = {
        "request": {
            "userGroupName": group_name,
            "criteria": [
                {
                    "criteriaKey": "designation",
                    "criteriaValue": designations,
                },
                {
                    "criteriaKey": "rootOrgId",
                    "criteriaValue": [org_id],
                },
            ],
        }
    }

    headers = {
        "Content-Type": "application/json",
        "accept": "application/json",
        "org": org,
        "rootorg": rootorg,
        "Authorization": f"{settings.KB_AUTH_TOKEN}",
        "x-authenticated-user-token": token,
        "x-authenticated-user-orgid": org_id,
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, json=payload, headers=headers, timeout=30.0)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"iGOT user group create API HTTP error: {e.response.status_code} | body={e.response.text}")
            raise HTTPException(
                status_code=502,
                detail=f"iGOT user group create API returned an error ({e.response.status_code}). Approval was not saved.",
            )
        except httpx.RequestError as e:
            logger.error(f"iGOT user group create API unreachable: {str(e)}")
            raise HTTPException(status_code=502, detail="iGOT user group create API is unreachable. Approval was not saved.")

    data = resp.json()
    user_group_id = data.get("result", {}).get("usergroupid")

    if not user_group_id:
        logger.error(f"iGOT user group create API response missing result.usergroupid: {data}")
        raise HTTPException(status_code=502, detail="iGOT user group create API did not return a user group ID. Approval was not saved.")

    return user_group_id


async def call_igot_create(
    token: str,
    org_id: str,
    plan_name: str,
    due_date: date,
    user_group_id: str,
    content_list: List[dict],
    plan_year: str,
    is_apar: bool = False,
    org: str = "dopt",
    rootorg: str = "igot",
    plan_type: str = "AI CBP-Non APAR",
) -> str:
    """
    POST to iGOT CBP plan create API. Returns the created plan ID.
    Raises HTTPException(502) on failure.
    """
    url = f"{settings.KB_BASE_URL}/api/cbplan/v4/create"

    payload = {
        "request": {
            "orgIdList": [org_id],
            "comment": f"{plan_name} published via MDO portal",
            "contentList": content_list,
            "contentType": "Course",
            "contextData": {
                "accessControl": {
                    "userGroups": [{"userGroupId": user_group_id}],
                    "version": 1,
                }
            },
            "endDate": due_date.strftime("%Y-%m-%d"),
            "isApar": is_apar,
            "planType": plan_type,
            "name": plan_name,
            "planYear": plan_year
        }
    }

    headers = {
        "Content-Type": "application/json",
        "accept": "application/json",
        "org": org,
        "rootorg": rootorg,
        "Authorization": f"{settings.KB_AUTH_TOKEN}",
        "x-authenticated-user-token": token,
        "x-authenticated-user-orgid": org_id,
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, json=payload, headers=headers, timeout=30.0)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"iGOT create API HTTP error: {e.response.status_code} | body={e.response.text}")
            raise HTTPException(
                status_code=502,
                detail=f"iGOT create API returned an error ({e.response.status_code}). Approval was not saved.",
            )
        except httpx.RequestError as e:
            logger.error(f"iGOT create API unreachable: {str(e)}")
            raise HTTPException(status_code=502, detail="iGOT create API is unreachable. Approval was not saved.")

    data = resp.json()
    plan_id = data.get("result", {}).get("id")

    if not plan_id:
        logger.error(f"iGOT create API response missing result.id: {data}")
        raise HTTPException(status_code=502, detail="iGOT create API did not return a plan ID. Approval was not saved.")

    return plan_id


async def call_igot_publish(
    token: str,
    org_id: str,
    plan_id: str,
    comment: str = "CBP plan approved",
    org: str = "dopt",
    rootorg: str = "igot",
) -> dict:
    """
    POST to iGOT CBP plan publish API. Returns the API response body.
    Raises HTTPException(502) on failure.
    """
    url = f"{settings.KB_BASE_URL}/api/cbplan/v4/publish"

    payload = {
        "request": {
            "id": plan_id,
            "comment": comment
        }
    }

    headers = {
        "Content-Type": "application/json",
        "accept": "application/json",
        "org": org,
        "rootorg": rootorg,
        "Authorization": f"{settings.KB_AUTH_TOKEN}",
        "x-authenticated-user-token": token,
        "x-authenticated-user-orgid": org_id,
        "x-authenticated-user-roles": ""
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, json=payload, headers=headers, timeout=30.0)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"iGOT publish API HTTP error: {e.response.status_code} | body={e.response.text}")
            raise HTTPException(
                status_code=502,
                detail=f"iGOT publish API returned an error ({e.response.status_code}). Publish failed.",
            )
        except httpx.RequestError as e:
            logger.error(f"iGOT publish API unreachable: {str(e)}")
            raise HTTPException(status_code=502, detail="iGOT publish API is unreachable. Publish failed.")

    data = resp.json()
    return data


async def call_igot_create_designation(
    token: str,
    designation: str,
    description: str = "",
) -> dict:
    """
    POST to iGOT designation master Create API.
    Creates the designation in the iGOT master list.
    Raises HTTPException(502) on failure.
    """
    url = f"{settings.KB_BASE_URL}/api/designation/create"

    payload = {
        "designation": designation,
        "description": description,
    }

    headers = {
        # "accept": "*/*",
        "Content-Type": "application/json",
        "Authorization": f"{settings.KB_AUTH_TOKEN}",
     }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, json=payload, headers=headers, timeout=30.0)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            # Handle "Already Present" as a non-error (designation exists in master list)
            try:
                error_body = e.response.json()
                errmsg = error_body.get("params", {}).get("errmsg", "")
                if errmsg == "Already Present":
                    return {"already_present": True, "message": "Designation is already present in the master list."}
            except Exception:
                pass
            logger.error(
                f"iGOT designation create API HTTP error: {e.response.status_code} | body={e.response.text}"
            )
            raise HTTPException(
                status_code=502,
                detail=f"iGOT designation create API returned an error ({e.response.status_code}).",
            )
        except httpx.RequestError as e:
            logger.error(f"iGOT designation create API unreachable: {str(e)}")
            raise HTTPException(
                status_code=502,
                detail="iGOT designation create API is unreachable.",
            )

    return resp.json()
