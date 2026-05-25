from .unifr_social_life_base_spider import UnifrSocialLifeBaseSpider


class UnifrActivitiesSpider(UnifrSocialLifeBaseSpider):
    """
    Crawl UNIFR activity pages related to social/campus life.
    Agenda links are intentionally excluded.

    Run:
        scrapy crawl unifr_activities -O spider_outputs/unifr_activities.json
    """

    name = "unifr_activities"
    start_urls = ["https://www.unifr.ch/campus/en/activities/"]

    TOPIC = "social_life_activities"
    SOURCE_TYPE = "unifr_social_life_activity_page"
    DOC_KEY_PREFIX = "unifr_activities"
    RECORD_TYPE = "activity_page"
    MAX_RECURSIVE_DEPTH = 1

    ALLOWED_PATH_PREFIXES = ("/campus/en/activities",)
    RELEVANT_LINK_KEYWORDS = {
        "activity", "activities", "sport", "sports", "culture", "cultural", "association",
        "student", "students", "choir", "music", "theatre", "event", "leisure", "community",
        "volunteer", "campus",
    }
    EXCLUDED_LINK_KEYWORDS = {"agenda", "calendar", "events.unifr.ch"}
