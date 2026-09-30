"""A Content-Security-Policy on every answer the API gives (owner rule R77).

HSTS, nosniff, the frame refusal and the referrer policy were already sent
(SecurityMiddleware, XFrameOptionsMiddleware, nginx). What was missing is the
list of what a page served from api.v-ent.co may load and run, so that
anything injected into one runs nowhere.

Four shapes, by what the response is:

- **JSON** (almost everything): nothing may load at all. A browser that opens
  an API address directly renders text and needs nothing else.
- **A file** (ticket PDF, CSV, calendar): no policy, see below.
- **Other HTML** (Django's own admin, error pages): only this origin, and never
  inside anybody else's frame.
- **An overlay** sets its own policy in serve_overlay, because OBS and the
  studio preview frame it and its runtime and pictures load from here. A
  response that already carries a policy is left alone.
"""

JSON_POLICY = ("default-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
               "form-action 'none'")

HTML_POLICY = ("default-src 'self'; img-src 'self' data: https:; "
               "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
               "font-src 'self' data:; object-src 'none'; base-uri 'self'; "
               "form-action 'self'; frame-ancestors 'none'")


class ContentSecurityPolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.has_header('Content-Security-Policy'):
            return response
        kind = (response.get('Content-Type') or '').split(';')[0].strip().lower()
        if kind == 'text/html':
            response['Content-Security-Policy'] = HTML_POLICY
        elif kind == 'application/json':
            response['Content-Security-Policy'] = JSON_POLICY
        # Anything else is a file (a ticket PDF, a CSV, a calendar entry). A
        # policy of 'none' on a PDF stops the browser's own viewer showing it,
        # and a file runs nothing anyway, so it is left as it is.
        return response
