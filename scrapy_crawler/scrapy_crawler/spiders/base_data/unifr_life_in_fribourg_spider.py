from .unifr_social_life_base_spider import UnifrSocialLifeBaseSpider


class UnifrLifeInFribourgSpider(UnifrSocialLifeBaseSpider):
    """
    Scrape the normal UNIFR Life in Fribourg page.

    Run:
        scrapy crawl unifr_life_in_fribourg -O spider_outputs/unifr_life_in_fribourg.json
    """

    name = "unifr_life_in_fribourg"
    start_urls = ["https://www.unifr.ch/studies/en/choose-fribourg/life-in-fribourg.html"]

    TOPIC = "social_life_life_in_fribourg"
    SOURCE_TYPE = "unifr_social_life_fribourg_page"
    DOC_KEY_PREFIX = "unifr_life_in_fribourg"
    RECORD_TYPE = "life_in_fribourg_page"
    MAX_RECURSIVE_DEPTH = 0
