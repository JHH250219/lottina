from datetime import datetime
from flask import Blueprint, Response, url_for

sitemap_bp = Blueprint("sitemap", __name__)


@sitemap_bp.route("/sitemap.xml")
def sitemap():
    pages = []

    static_pages = [
        ("index", "weekly"),
        ("results", "daily"),
        ("login", "monthly"),
        ("register", "monthly"),
    ]

    for endpoint, freq in static_pages:
        pages.append(
            {
                "loc": url_for(endpoint, _external=True),
                "priority": "0.8",
                "changefreq": freq,
                "lastmod": datetime.utcnow().date().isoformat(),
            }
        )

    xml = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]

    for page in pages:
        xml.append("<url>")
        xml.append(f"<loc>{page['loc']}</loc>")
        xml.append(f"<lastmod>{page['lastmod']}</lastmod>")
        xml.append(f"<changefreq>{page['changefreq']}</changefreq>")
        xml.append(f"<priority>{page['priority']}</priority>")
        xml.append("</url>")

    xml.append("</urlset>")

    return Response("\n".join(xml), mimetype="application/xml")
