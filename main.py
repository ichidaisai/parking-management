from fastapi import APIRouter, FastAPI

auth = APIRouter(
    prefix="/auth",
    tags=["auth"],
    responses={
        200: {"description": "Successful Response"},
        403: {"description": "Forbidden"},
        404: {"description": "Not found"},
    },
)


@auth.get("/user/register")
async def create_user() -> dict:
    """
    ユーザを作成する関数。その時、パスキーも必ず登録する
    """
    return {"message": "User created successfully"}


@auth.get("/user/register/verify")
async def create_verify_user() -> dict:
    """
    ユーザ登録を検証する関数。リダイレクトもする
    """
    return {"message": "User logged in successfully"}


@auth.post("/user/login")
async def login_user() -> dict:
    """
    ユーザをログインする関数
    """
    return {"message": "User logged in successfully"}


@auth.post("/user/verify")
async def login_verify_user() -> dict:
    """
    ユーザを検証する関数
    """
    return {"message": "User logged in successfully"}


app = FastAPI()
app.include_router(auth)
