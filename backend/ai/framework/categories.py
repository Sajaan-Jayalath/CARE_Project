"""The five approved CARE categories and the shared framework loader."""
import json
from enum import Enum
from pathlib import Path


class Category(str, Enum):
    GENDERED_LANGUAGE = "Gendered Language"
    GENDER_STEREOTYPES = "Gender Stereotypes"
    REPRESENTATION = "Representation"
    VISUAL_REPRESENTATION = "Visual Representation"
    PROFESSIONAL_ROLES = "Professional Roles"


class Level(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


FRAMEWORK_PATH = Path(__file__).with_name("criteria.json")


def load_framework() -> dict:
    framework = json.loads(FRAMEWORK_PATH.read_text(encoding="utf-8-sig"))
    categories = [item["category"] for item in framework["categories"]]
    if len(categories) != 5 or set(categories) != {c.value for c in Category}:
        raise ValueError("The framework must contain exactly the five CARE categories.")
    return framework
