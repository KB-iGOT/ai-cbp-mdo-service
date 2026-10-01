"""
Helper for calling the iGOT user group create and CBP plan create & publish APIs.
"""
from datetime import date
from typing import List

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
                    {"identifier": identifier, "mandatory": bool(course.get("mandatory", True))}
                )
    return content_list


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
            "usergroupname": group_name,
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
