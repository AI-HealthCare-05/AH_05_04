from pydantic import BaseModel, ConfigDict


class BaseSerializerModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
