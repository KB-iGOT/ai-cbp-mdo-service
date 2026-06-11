
from fastapi import APIRouter



from .mdo_approval import router as mdo_approval_router
from .designation_approval import router as designation_approval_router
from .kb_apis import router as kb_apis_router

router = APIRouter(prefix="/v1")

router.include_router(mdo_approval_router)
router.include_router(designation_approval_router)
router.include_router(kb_apis_router)
