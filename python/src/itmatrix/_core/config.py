"""Client defaults, defined once for the Python package.

``DEFAULT_BASE_URL`` is the only default API origin in the Python package.
Pass ``base_url=`` to use another origin.
Once the pinned OpenAPI document declares ``servers``, this value must equal
``servers[0].url``; the repository's public-boundary check enforces that.
"""

DEFAULT_BASE_URL = "https://api.itmatrixhq.com"
