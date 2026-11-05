from .amazon import Amazon


class AmazonAU(Amazon):
    name = "amazon_au"
    region = "au"
    base = "https://www.amazon.com.au"
