from .unifr_social_life_base_spider import UnifrSocialLifeBaseSpider


class UnifrInfrastructuresSpider(UnifrSocialLifeBaseSpider):
    """
    Crawl UNIFR infrastructure pages related to social/campus life.
    Map & Orientation is intentionally excluded.

    Run:
        scrapy crawl unifr_infrastructures -O spider_outputs/unifr_infrastructures.json
    """

    name = "unifr_infrastructures"
    start_urls = ["https://www.unifr.ch/campus/en/infrastructures/"]

    TOPIC = "social_life_infrastructures"
    SOURCE_TYPE = "unifr_social_life_infrastructure_page"
    DOC_KEY_PREFIX = "unifr_infrastructures"
    RECORD_TYPE = "infrastructure_page"
    MAX_RECURSIVE_DEPTH = 1

    ALLOWED_PATH_PREFIXES = ("/campus/en/infrastructures",)
    RELEVANT_LINK_KEYWORDS = {
        "infrastructure", "library", "libraries", "restaurant", "cafeteria", "sport", "sports",
        "mobility", "parking", "it", "computer", "room", "housing", "shop", "building",
        "campus", "services", "facilities",
    }
    EXCLUDED_LINK_KEYWORDS = {"map", "orientation", "maps/orientation", "agenda", "calendar"}
