"""
Karmayogi Bharat API endpoints.
Allows MDO admins to fetch course suggestions and search designations.
"""
import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Body


from ...core.auth import require_role
from ...core.configs import settings
from ...core.logger import logger

router = APIRouter(
    prefix="",
    tags=["Karmayogi Bharat APIs"],
)

def _get_headers() -> dict:
    return {
        "Content-Type": "application/json",
        "Authorization": f"{settings.KB_AUTH_TOKEN}",
    }

# iGOT Course Suggestion APIs
@router.post("/course/suggestions")
async def fetch_course_from_igot_platform(
    body: dict = Body(...),
    auth: tuple = Depends(require_role(['MDO_ADMIN','MDO_LEADER'])),
):
    """
    fetch courses from iGOT platform
    Returns:
        Course suggestions with pagination info
    """
    # body = await request.json()
    logger.info(f"Fetching course suggestion requuest: {body}")
    try:
        # Use an async HTTP client to make the request
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.KB_BASE_URL}/api/content/v1/search",
                json=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"{settings.KB_AUTH_TOKEN}"
                }
            )
            response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
            
            # Parse the JSON response
            data = response.json()
            logger.info("Fetched list of courses from iGOT platform")
            return data
    except httpx.HTTPStatusError as e:
        logger.error(f"fetch courses from iGOT platform Upstream error: {e.response.text}")
        raise HTTPException(
            status_code=e.response.status_code,
            detail=e.response.text,
        )
    except Exception as e:
        logger.error(f"Error fetching the list of courses from iGOT platform: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch the list of courses from iGOT platform"
        )

@router.post(
    "/designation/search",
    status_code=status.HTTP_200_OK,
)
async def search_designations(
    body: dict = Body(default={"filterCriteriaMap":{"status":"Active"},"requestedFields":[],"pageNumber":0,"pageSize":50}),
    auth: tuple = Depends(require_role(['MDO_ADMIN','MDO_LEADER'])),
):
    """
    Search iGOT designations via the Karmayogi Bharat portal API.
    """
    try:
        logger.info(f"Searching designations with body: {body}")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                settings.KB_BASE_URL + "/api/designation/search",
                json=body,
                headers=_get_headers(),
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.exception("Unexpected error while searching designations:")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch designations from iGOT portal",
        )
