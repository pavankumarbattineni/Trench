from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    db: str
    # The git commit this instance was built from (Render sets
    # RENDER_GIT_COMMIT); "dev" when running anywhere else. The deploy
    # pipeline polls this to confirm a new release is actually live.
    version: str
