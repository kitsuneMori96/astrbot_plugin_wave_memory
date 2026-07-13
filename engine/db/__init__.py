"""engine.db — 数据层拆分包"""

from .connection import ConnectionManager
from .memory_repo import MemoryRepo
from .scoped_knowledge_repo import ScopedKnowledgeRepo, ScopedKnowledgeScopeError
from .tag_repo import TagRepo
from .social_repo import SocialRepo
from .knowledge_repo import KnowledgeRepo
from .booklore_repo import BookLoreRepo
from .book_experience_repo import BookExperienceEpisodeRepository
from .learning_repository import (
    LearningCandidateRepository,
    LearningIdempotencyConflict,
    LearningJobRepository,
    LearningPromotionRepository,
    LearningRepositories,
    LearningRepositoryError,
    LearningRepositoryIntegrityError,
    LearningSourceRepository,
)
from .learning_types import CandidateType, PromotionStatus, ReviewStatus, TargetKind

__all__ = [
    "ConnectionManager",
    "MemoryRepo",
    "ScopedKnowledgeRepo",
    "ScopedKnowledgeScopeError",
    "TagRepo",
    "SocialRepo",
    "KnowledgeRepo",
    "BookLoreRepo",
    "BookExperienceEpisodeRepository",
    "CandidateType",
    "LearningCandidateRepository",
    "LearningIdempotencyConflict",
    "LearningJobRepository",
    "LearningPromotionRepository",
    "LearningRepositories",
    "LearningRepositoryError",
    "LearningRepositoryIntegrityError",
    "LearningSourceRepository",
    "PromotionStatus",
    "ReviewStatus",
    "TargetKind",
]
