# -*- coding: utf-8 -*-
"""
    zine.importers.feed
    ~~~~~~~~~~~~~~~~~~~

    This importer can import web feeds.  Currently it is limited to ATOM
    plus optional Zine extensions.

    :copyright: (c) 2008 by the Zine Team, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""
import urllib
from pickle import loads
from lxml import etree
from os.path import join, dirname
from zine.application import get_application
from zine.i18n import _, lazy_gettext
from zine.importers import Importer, Blog, Tag, Category, Author, Post, Comment
from zine.importers.feed import Extension
from zine.utils import log, forms
from zine.utils.admin import flash
from zine.utils.dates import parse_iso8601
from zine.utils.xml import Namespace, to_text
from zine.utils.http import redirect_to
from zine.utils.zeml import load_parser_data
from zine.utils.validators import is_valid_url
from zine.utils.exceptions import UserException
from zine.zxa import ATOM_NS, XML_NS

TEXTPRESS_NS = 'http://textpress.pocoo.org/'
TEXTPRESS_TAG_URI = TEXTPRESS_NS + '#tag-scheme'
TEXTPRESS_CATEGORY_URI = TEXTPRESS_NS + '#category-scheme'
BUGS_LINK = "http://zine.ufsoft.org/newticket?keywords=textpress_export" + \
            "&component=Textpress%20Importer"

atom = Namespace(ATOM_NS)
xml = Namespace(XML_NS)
textpress = Namespace(TEXTPRESS_NS)


def _get_text_content(elements):
    """Return the text content from the best element match."""
    if not elements:
        return u''
    for element in elements:
        if element.attrib.get('type') == 'text':
            return element.text or u''
    for element in elements:
        if element.attrib.get('type') == 'html':
            return to_text(element)
    return to_text(elements[0])


def _get_html_content(elements):
    """Returns the html content from the best element match or another
    content treated as html.  This is totally against the specification
    but this importer assumes that the text representation is unprocessed
    markup language from the blog.  This is most likely a dialect of HTML
    or a lightweight markup language in which case the importer only has
    to switch the parser afterwards.
    """
    if not elements:
        return u''
    for element in elements:
        if element.attrib.get('type') == 'html':
            return element.text
    return elements[0].text


def _to_bool(value):
    if isinstance(value, bool):
        return value
    value = value.strip()
    if value == 'yes':
        return True
    elif value == 'no':
        return False
    raise ValueError('invalid boolean literal, expected yes/no')


def _pickle(value):
    if value:
        return loads(value.decode('base64'))


def _parser_data(value):
    if value:
        return load_parser_data(value.decode('base64'))


def parse_feed(fd):
    tree = etree.parse(fd).getroot()
    if tree.tag == 'rss':
        parser_class = RSSParser
    elif tree.tag == atom.feed:
        parser_class = AtomParser
    else:
        raise FeedImportError(_('Unknown feed uploaded.'))
    parser = parser_class(tree)
    parser.parse()
    return parser.blog


class TPParser(object):
    feed_type = None

    def __init__(self, tree):
        self.app = get_application()
        self.tree = tree
        self.tags = []
        self.categories = []
        self.authors = []
        self.posts = []
        self.blog = None
        self.extensions = [extension(self.app, self, tree)
                           for extension in self.app.feed_importer_extensions
                           if self.feed_type in extension.feed_types]

    def find_tag(self, **critereon):
        return self._find_criteron(self.tags, critereon)

    def find_category(self, **critereon):
        return self._find_criteron(self.categories, critereon)

    def find_author(self, **critereon):
        return self._find_criteron(self.authors, critereon)

    def find_post(self, **critereon):
        return self._find_criteron(self.posts, critereon)

    def _find_criteron(self, sequence, d):
        if len(d) != 1:
            raise TypeError('one critereon expected')
        key, value = d.iteritems().next()
        for item in sequence:
            if getattr(item, key, None) == value:
                return item


class RSSParser(TPParser):
    feed_type = 'rss'

    def __init__(self, tree):
        raise FeedImportError(_('Importing of RSS feeds is currently '
                                'not possible.'))


class AtomParser(TPParser):
    feed_type = 'atom'

    def __init__(self, tree):
        TPParser.__init__(self, tree)

        # use for the category fallback handling if no extension
        # takes over the handling.
        self._categories_by_term = {}

        # and the same for authors
        self._authors_by_username = {}
        self._authors_by_email = {}

    def parse(self):
        for entry in self.tree.findall(atom.entry):
            self.posts.append(self.parse_post(entry))

        self.blog = Blog(
            self.tree.findtext(atom.title),
            self.tree.findtext(atom.link),
            self.tree.findtext(atom.subtitle),
            self.tree.attrib.get(xml.lang, u'en'),
            self.tags,
            self.categories,
            self.posts,
            self.authors
        )
        self.blog.element = self.tree
        for extension in self.extensions:
            extension.handle_root(self.blog)

    def parse_post(self, entry):
        # parse the dates first.
        updated = parse_iso8601(entry.findtext(atom.updated))
        published = entry.findtext(atom.published)
        if published is not None:
            published = parse_iso8601(published)
        else:
            published = updated

        # figure out tags and categories by invoking the
        # callbacks on the extensions first.  If no extension
        # was able to figure out what to do with it, we treat it
        # as category.
        tags, categories = self.parse_categories(entry)

        link = entry.find(atom.link)
        if link is not None:
            link = link.attrib.get('href')

        post_parser = _pickle(entry.findall(textpress.data)[0].text).get('parser', 'html')
        if post_parser not in get_application().parsers:
            post_parser = 'html'

        post = Post(
            entry.findtext(textpress.slug),                 # slug
            _get_text_content(entry.findall(atom.title)),   # title
            link,                                           # link
            published,                                      # pub_date
            self.parse_author(entry),                       # author
            # XXX: the Post is prefixing the intro before the actual
            # content.  This is the default Zine behavior and makes sense
            # for Zine.  However nearly every blog works differently and
            # treats summary completely different from content.  We should
            # think about that.
            _get_html_content(entry.findall(atom.summary)), # intro
            _get_html_content(entry.findall(atom.content)), # body
            tags,                                           # tags
            categories,                                     # categories
            parser=post_parser,
            updated=updated,
            uid=entry.findtext(atom.id)
        )
        post.element = entry
        content_type = entry.findtext(textpress.content_type)
        if content_type not in ('page', 'entry'):
            post.content_type = 'entry'

        # now parse the comments for the post
        self.parse_comments(post)

        for extension in self.extensions:
            extension.postprocess_post(post)

        return post

    def parse_author(self, entry):
        """Lookup the author for the given entry."""
        def _remember_author(author):
            if author.email is not None and \
               author.email not in self._authors_by_email:
                self._authors_by_email[author.email] = author
            if author.username is not None and \
               author.username not in self._authors_by_username:
                self._authors_by_username[author.username] = author

        author = entry.find(atom.author)
        email = author.findtext(atom.email)
        username = author.findtext(atom.name)

        for extension in self.extensions:
            rv = extension.lookup_author(author, entry, username, email)
            if rv is not None:
                _remember_author(rv)
                return rv

        if email is not None and email in self._authors_by_email:
            return self._authors_by_email[email]
        if username in self._authors_by_username:
            return self._authors_by_username[username]

        author = Author(username, email)
        _remember_author(author)
        self.authors.append(author)
        return author

    def parse_categories(self, entry):
        """Is passed an <entry> element and parses all <category>
        child elements.  Returns a tuple with ``(tags, categories)``.
        """
        def _remember_category(category, element):
            term = element.attrib['term']
            if term not in self._categories_by_term:
                self._categories_by_term[term] = category

        tags = []
        categories = []

        for category in entry.findall(atom.category):
            for extension in self.extensions:
                rv = extension.tag_or_category(category)
                if rv is not None:
                    if isinstance(rv, Category):
                        categories.append(rv)
                        _remember_category(rv, category)
                    else:
                        tags.append(rv)
                    break
            else:
                rv = self._categories_by_term.get(category.attrib['term'])
                if rv is None:
                    rv = Category(category.attrib['term'],
                                  category.attrib.get('label'))
                    _remember_category(rv, category)
                    self.categories.append(rv)
                categories.append(rv)
        return tags, categories

    def parse_comments(self, post):
        """Parse the comments for the post."""
        for extension in self.extensions:
            post.comments.extend(extension.parse_comments(post) or ())


class FeedImportError(UserException):
    """Raised if the system was unable to import the feed."""

class FeedImportForm(forms.Form):
    """This form is used in the Textpress importer."""
    download_url = forms.TextField(
        lazy_gettext(u'Textpress Export File Download URL'),
        validators=[is_valid_url()])

class TextPressFeedImporter(Importer):
    name = 'textpress-feed'
    title = lazy_gettext(u'TextPress Importer')

    def configure(self, request):
        form = FeedImportForm()

        if request.method == 'POST' and form.validate(request.form):
            feed = request.files.get('feed')
            if form.data['download_url']:
                if not form.data['download_url'].endswith('.tpxa'):
                    error = _(u"Don't pass a real feed URL, it should be a "
                              u"regular URL where you're serving the file "
                              u"generated with the textpress_exporter.py script")
                    flash(error, 'error')
                    return self.render_admin_page('import_textpress.html',
                                                  form=form.as_widget(),
                                                  bugs_link=BUGS_LINK)
                try:
                    feed = urllib.urlopen(form.data['download_url'])
                except Exception, e:
                    error = _(u'Error downloading from URL: %s') % e
                    flash(error, 'error')
                    return self.render_admin_page('import_textpress.html',
                                                  form=form.as_widget(),
                                                  bugs_link=BUGS_LINK)
            elif not feed:
                return redirect_to('import/feed')

            try:
                blog = parse_feed(feed)
            except Exception, e:
                log.exception(_(u'Error parsing uploaded file'))
                print repr(e)
                flash(_(u'Error parsing feed: %s') % e, 'error')
            else:
                self.enqueue_dump(blog)
                flash(_(u'Added imported items to queue.'))
                return redirect_to('admin/import')

        return self.render_admin_page('import_textpress.html',
                                      form=form.as_widget(),
                                      bugs_link=BUGS_LINK)


class TPZEAExtension(Extension):
    """Handles Zine Atom extensions.  This extension can handle the extra
    namespace used for ZEA feeds as generated by the Zine export.  Because
    in a feed with Zine extensions the rules are pretty strict we don't
    look up authors, tags or categories on the parser object like we should
    but have a mapping for those directly on the extension.
    """

    feed_types = frozenset(['atom'])

    def __init__(self, app, parser, root):
        Extension.__init__(self, app, parser, root)
        self._authors = {}
        self._tags = {}
        self._categories = {}
        self._dependencies = root.find(textpress.dependencies)

        self._lookup_user = etree.XPath('tp:user[@tp:dependency=$id]',
                                        namespaces={'tp': TEXTPRESS_NS})

    def _parse_config(self, element):
        result = {}
        if element is not None:
            for element in element.findall(textpress.item):
                result[element.attrib['key']] = element.text
        return result

    def _get_author(self, dependency):
        author = self._authors.get(dependency)
        if author is None:
            element = self._lookup_user(self._dependencies, id=dependency)[0]
            author = Author(
                element.findtext(textpress.username),
                element.findtext(textpress.email),
                element.findtext(textpress.real_name),
                element.findtext(textpress.description),
                element.findtext(textpress.www),
                element.findtext(textpress.pw_hash),
                int(element.findtext(textpress.role) or 0)==4,
                _pickle(element.findtext(textpress.extra))
            )
            for privilege in element.findall(textpress.privilege):
                p = self.app.privileges.get(privilege.text)
                if p is not None:
                    author.privileges.add(p)
            self._authors[dependency] = author
            self.parser.authors.append(author)
        return author

    def _parse_tag(self, element):
        term = element.attrib['term']
        if term not in self._tags:
            self._tags[term] = Tag(term, element.attrib.get('label'))
        return self._tags[term]

    def _parse_category(self, element):
        term = element.attrib['term']
        if term not in self._categories:
            self._categories[term] = Category(
                term, element.attrib.get('label'),
                element.findtext(textpress.description)
            )
        return self._categories[term]

    def handle_root(self, blog):
        blog.configuration.update(self._parse_config(
            blog.element.find(textpress.configuration)))

    def postprocess_post(self, post):
        content_type = post.element.findtext(textpress.content_type)
        if content_type is not None:
            post.content_type = content_type

    def lookup_author(self, author, entry, username, email):
        dependency = author.attrib.get(textpress.dependency)
        if dependency is not None:
            return self._get_author(dependency)

    def tag_or_category(self, element):
        scheme = element.attrib.get('scheme')
        if scheme == TEXTPRESS_TAG_URI:
            return self._parse_tag(element)
        elif scheme == TEXTPRESS_CATEGORY_URI:
            return self._parse_category(element)

    def parse_comments(self, post):
        comments = {}
        unresolved_parents = {}

        for element in post.element.findall(textpress.comment):
            author = element.find(textpress.author)
            dependency = author.attrib.get('dependency')
            if dependency is not None:
                author = self._get_author(author)
                email = www = None
            else:
                email = author.findtext(textpress.email)
                www = author.findtext(textpress.uri)
                author = author.findtext(textpress.name)

            body = element.findall(textpress.data)
            if body:
                pickled = _pickle(body[0].text)
                body = pickled.get('raw_body', u'')

                comment_parser = pickled.get('parser', 'html')
                if comment_parser not in get_application().parsers:
                    comment_parser = 'html'

            comment = Comment(
                author, body, email, www, None,
                parse_iso8601(element.findtext(textpress.published)),
                element.findtext(textpress.submitter_ip), comment_parser,
                _to_bool(element.findtext(textpress.is_pingback)),
                int(element.findtext(textpress.status)),
                element.findtext(textpress.blocked_msg),
                _parser_data(element.findtext(textpress.parser_data))
            )
            comments[int(element.findtext(textpress.id))] = comment
            parent = element.findtext(textpress.parent)
            if parent is not None or '':
                unresolved_parents[comment] = int(parent)

        for comment, parent_id in unresolved_parents.iteritems():
            comment.parent = comments[parent_id]

        return comments.values()


def setup(app, plugin):
    SHARED_FILES = join(dirname(__file__), 'shared')
    TEMPLATE_FILES = join(dirname(__file__), 'templates')

    app.add_feed_importer_extension(TPZEAExtension)
    app.add_template_searchpath(TEMPLATE_FILES)
    app.add_shared_exports('textpress_importer', SHARED_FILES)
    app.add_importer(TextPressFeedImporter)
