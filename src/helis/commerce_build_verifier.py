from __future__ import annotations

import re

from helis.commerce_domain import CommerceBuildContext
from helis.domain import BuildBundle, BuildCheck, BuildSpec, BuildTemplate


_HREF_PATTERN = re.compile(r"\bhref\s*=\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)


def _check(name: str, passed: bool, details: str) -> BuildCheck:
    return BuildCheck(name=name, passed=passed, details=details)


class CommerceBuildVerifier:
    """Deterministically proves that a build did not broaden an approved checkout contract."""

    def verify(
        self,
        spec: BuildSpec,
        bundle: BuildBundle,
        commerce: CommerceBuildContext | None,
    ) -> list[BuildCheck]:
        if commerce is None:
            return []
        if spec.template != BuildTemplate.STATIC_WEB:
            return [
                _check(
                    "commerce_static_web_only",
                    False,
                    "self-serve checkout builds must use static_web_v1",
                )
            ]

        index = next((item.content for item in bundle.files if item.path == "index.html"), "")
        hrefs = _HREF_PATTERN.findall(index)
        http_hrefs = [
            href for href in hrefs if href.lower().startswith(("http://", "https://"))
        ]
        exact_checkout_count = sum(href == commerce.checkout_url for href in hrefs)
        alternate_http = sorted({href for href in http_hrefs if href != commerce.checkout_url})
        return [
            _check(
                "commerce_checkout_exact",
                exact_checkout_count >= 1,
                "index.html must link to the exact approved checkout URL",
            ),
            _check(
                "commerce_no_alternate_http_destinations",
                not alternate_http,
                (
                    "no alternate external HTTP(S) destinations found"
                    if not alternate_http
                    else f"forbidden external destinations: {alternate_http}"
                ),
            ),
            _check(
                "commerce_price_exact",
                commerce.display_price in index,
                f"index.html must show exact approved price {commerce.display_price}",
            ),
            _check(
                "commerce_offer_hash_bound",
                len(commerce.offer_hash) == 64,
                f"approved offer hash={commerce.offer_hash}",
            ),
        ]
