from .unifr_social_life_base_spider import UnifrSocialLifeBaseSpider


class UnifrLivingInFribourgSpider(UnifrSocialLifeBaseSpider):
    """
    Scrape the normal UNIFR Living in Fribourg page.

    Run:
        scrapy crawl unifr_living_in_fribourg -O spider_outputs/unifr_living_in_fribourg.json
    """

    name = "unifr_living_in_fribourg"
    start_urls = ["https://www.unifr.ch/studies/en/choose-fribourg/habiter-fribourg.html"]

    TOPIC = "social_life_living_in_fribourg"
    SOURCE_TYPE = "unifr_social_life_living_page"
    DOC_KEY_PREFIX = "unifr_living_in_fribourg"
    RECORD_TYPE = "living_in_fribourg_page"
    MAX_RECURSIVE_DEPTH = 0
