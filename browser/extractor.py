"""
Extractor — Pulls structured data from the current page DOM.
Provides the LLM with a clear picture of what's on the page:
interactive elements, text content, forms, navigation, and page structure.
"""
from __future__ import annotations

from playwright.async_api import Page


class Extractor:
    """Extracts structured page data for the LLM to reason about."""

    def __init__(self, page: Page):
        self._page = page

    async def extract_page_state(self) -> dict:
        """Full page state extraction — the LLM's 'eyes' into the page."""
        return await self._page.evaluate("""
            (() => {
                // ── Helper: generate a reliable CSS selector ──
                function getSelector(el, depth) {
                    if (depth === undefined) depth = 0;
                    if (depth > 5) return el.tagName.toLowerCase();
                    try {
                        if (el.id) {
                            var escaped = CSS.escape(el.id);
                            var sel = '#' + escaped;
                            try { document.querySelector(sel); return sel; } catch(e) {}
                        }
                        if (el.getAttribute('data-testid')) {
                            return '[data-testid="' + el.getAttribute('data-testid') + '"]';
                        }
                        if (el.getAttribute('name')) {
                            var name = el.getAttribute('name');
                            var sel = el.tagName.toLowerCase() + '[name="' + name.replace(/"/g, '\\\\"') + '"]';
                            try {
                                if (document.querySelectorAll(sel).length === 1) return sel;
                            } catch(e) {}
                        }
                        if (el.getAttribute('aria-label')) {
                            var sel = el.tagName.toLowerCase() + '[aria-label="' + el.getAttribute('aria-label').replace(/"/g, '\\\\"') + '"]';
                            try {
                                if (document.querySelectorAll(sel).length === 1) return sel;
                            } catch(e) {}
                        }
                        var validClasses = Array.from(el.classList || []).filter(function(c) {
                            return /^[a-zA-Z_-][a-zA-Z0-9_-]*$/.test(c);
                        }).slice(0, 2);
                        if (validClasses.length > 0) {
                            var sel = el.tagName.toLowerCase() + '.' + validClasses.join('.');
                            try {
                                if (document.querySelectorAll(sel).length === 1) return sel;
                            } catch(e) {}
                        }
                        var parent = el.parentElement;
                        if (!parent) return el.tagName.toLowerCase();
                        var siblings = Array.from(parent.children).filter(function(c) { return c.tagName === el.tagName; });
                        var idx = siblings.indexOf(el) + 1;
                        return getSelector(parent, depth + 1) + ' > ' + el.tagName.toLowerCase() + ':nth-child(' + idx + ')';
                    } catch(e) {
                        return el.tagName.toLowerCase();
                    }
                }

                function getLabel(el) {
                    return (
                        el.getAttribute('aria-label') ||
                        el.getAttribute('title') ||
                        (el.textContent || '').trim().slice(0, 80) ||
                        el.getAttribute('placeholder') ||
                        el.getAttribute('name') ||
                        ''
                    );
                }

                function isVisible(el) {
                    if (!el.offsetParent && el.tagName !== 'BODY' && el.tagName !== 'HTML') return false;
                    var rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                }

                // ── Page metadata ──
                var state = {
                    url: window.location.href,
                    title: document.title,
                    meta_description: '',
                    links: [],
                    buttons: [],
                    inputs: [],
                    selects: [],
                    forms: [],
                    images: [],
                    headings: [],
                    visible_text_summary: '',
                };

                var metaDesc = document.querySelector('meta[name="description"]');
                if (metaDesc) state.meta_description = metaDesc.content || '';

                // ── Links ──
                document.querySelectorAll('a[href]').forEach(function(el) {
                    if (!isVisible(el)) return;
                    var href = el.href || '';
                    if (href.startsWith('javascript:') || href.startsWith('mailto:') || href.startsWith('tel:')) return;
                    state.links.push({
                        selector: getSelector(el),
                        href: href,
                        text: getLabel(el),
                    });
                });

                // ── Buttons ──
                document.querySelectorAll('button, [role="button"], input[type="submit"], input[type="button"]').forEach(function(el) {
                    if (!isVisible(el)) return;
                    state.buttons.push({
                        selector: getSelector(el),
                        text: getLabel(el),
                        type: el.type || 'button',
                        disabled: el.disabled || false,
                    });
                });

                // ── Inputs ──
                document.querySelectorAll('input:not([type="submit"]):not([type="button"]):not([type="hidden"]), textarea').forEach(function(el) {
                    if (!isVisible(el)) return;
                    var label = '';
                    if (el.id) {
                        var labelEl = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                        if (labelEl) label = labelEl.textContent.trim();
                    }
                    state.inputs.push({
                        selector: getSelector(el),
                        type: el.type || 'text',
                        name: el.name || '',
                        placeholder: el.placeholder || '',
                        label: label,
                        value: el.value || '',
                        required: el.required || false,
                        disabled: el.disabled || false,
                    });
                });

                // ── Selects ──
                document.querySelectorAll('select').forEach(function(el) {
                    if (!isVisible(el)) return;
                    state.selects.push({
                        selector: getSelector(el),
                        name: el.name || '',
                        options: Array.from(el.options).map(function(o) {
                            return { value: o.value, text: o.text };
                        }).slice(0, 20),
                        selected: el.value,
                    });
                });

                // ── Forms ──
                document.querySelectorAll('form').forEach(function(el) {
                    state.forms.push({
                        selector: getSelector(el),
                        action: el.action || '',
                        method: (el.method || 'get').toUpperCase(),
                        field_count: el.elements.length,
                    });
                });

                // ── Images (check for broken ones) ──
                document.querySelectorAll('img').forEach(function(el) {
                    if (!isVisible(el)) return;
                    state.images.push({
                        src: el.src || '',
                        alt: el.alt || '',
                        broken: el.naturalWidth === 0 && el.complete,
                        width: el.width,
                        height: el.height,
                    });
                });

                // ── Headings (page structure) ──
                document.querySelectorAll('h1, h2, h3').forEach(function(el) {
                    var text = (el.textContent || '').trim();
                    if (text) {
                        state.headings.push({
                            level: parseInt(el.tagName[1]),
                            text: text.slice(0, 100),
                        });
                    }
                });

                // ── Visible text summary (first ~500 chars) ──
                var bodyText = (document.body?.innerText || '').trim();
                state.visible_text_summary = bodyText.slice(0, 500);

                return state;
            })()
        """) or {}

    async def extract_form_details(self, form_selector: str) -> dict:
        """Deep extraction of a specific form's fields and validation rules."""
        return await self._page.evaluate(f"""
            (() => {{
                var form = document.querySelector({repr(form_selector)});
                if (!form) return null;

                var fields = [];
                var elements = form.elements;
                for (var i = 0; i < elements.length; i++) {{
                    var el = elements[i];
                    if (el.type === 'hidden') continue;

                    var label = '';
                    if (el.id) {{
                        var labelEl = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                        if (labelEl) label = labelEl.textContent.trim();
                    }}

                    fields.push({{
                        tag: el.tagName.toLowerCase(),
                        type: el.type || 'text',
                        name: el.name || '',
                        placeholder: el.placeholder || '',
                        label: label,
                        required: el.required || false,
                        pattern: el.pattern || '',
                        min: el.min || '',
                        max: el.max || '',
                        maxlength: el.maxLength > 0 ? el.maxLength : null,
                        value: el.value || '',
                        disabled: el.disabled || false,
                        options: el.tagName === 'SELECT'
                            ? Array.from(el.options).map(function(o) {{ return {{ value: o.value, text: o.text }}; }})
                            : [],
                    }});
                }}

                return {{
                    action: form.action || '',
                    method: (form.method || 'get').toUpperCase(),
                    fields: fields,
                }};
            }})()
        """)

    async def get_visible_text(self) -> str:
        """Full visible text of the page."""
        try:
            return await self._page.evaluate("document.body?.innerText || ''")
        except Exception:
            return ""

    async def check_visual_issues(self) -> list[dict]:
        """Detect common visual problems: broken images, empty containers, overflow."""
        return await self._page.evaluate("""
            (() => {
                var issues = [];

                // Broken images
                document.querySelectorAll('img').forEach(function(el) {
                    if (el.complete && el.naturalWidth === 0 && el.src) {
                        issues.push({
                            type: 'broken_image',
                            description: 'Image failed to load: ' + el.src.slice(0, 100),
                            selector: el.id ? '#' + CSS.escape(el.id) : 'img[src="' + el.src.slice(0, 50) + '"]',
                        });
                    }
                });

                // Elements overflowing viewport
                var vw = window.innerWidth;
                document.querySelectorAll('*').forEach(function(el) {
                    var rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.right > vw + 10 && el.tagName !== 'HTML' && el.tagName !== 'BODY') {
                        var tag = el.tagName.toLowerCase();
                        if (['div', 'section', 'main', 'table', 'form', 'img'].indexOf(tag) !== -1) {
                            issues.push({
                                type: 'overflow',
                                description: tag + ' element overflows viewport by ' + Math.round(rect.right - vw) + 'px',
                                selector: el.id ? '#' + CSS.escape(el.id) : tag,
                            });
                        }
                    }
                });

                // Empty containers that look like they should have content
                document.querySelectorAll('[class*="content"], [class*="list"], [class*="container"], [class*="body"], main, article').forEach(function(el) {
                    var rect = el.getBoundingClientRect();
                    if (rect.height < 5 && rect.width > 100) {
                        var text = (el.textContent || '').trim();
                        if (!text && el.children.length === 0) {
                            issues.push({
                                type: 'empty_container',
                                description: 'Empty container that may be missing content',
                                selector: el.id ? '#' + CSS.escape(el.id) : el.tagName.toLowerCase() + '.' + (el.className || '').split(' ')[0],
                            });
                        }
                    }
                });

                return issues.slice(0, 20);
            })()
        """) or []
