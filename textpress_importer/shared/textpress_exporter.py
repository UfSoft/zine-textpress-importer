#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
    Download this file into where you host your production TextPress Blog.
    Once downloaded, execute it and pass `-h` in order to have a look at it's
    options.

    The implementation was copied and changed according to needs from the
    newest revision of TextPress, right before the TextPress to Zine name
    changes.

    textpress.tpxa
    ~~~~~~~~~~~~~~

    TextPress eXtended Atom.  This module sounds like it has a ridiculous
    implementation but that's actually intention.  The main reason is that
    because this file can (and does) become incredible big for bigger blogs
    and elementtree does not really support what we try to achieve here we
    somewhat hack around the limitation by using a separate element tree for
    each item and wrap it in hand written XML.

    :copyright: Copyright 2008 by Armin Ronacher, 2009 by Pedro Algarvio.
    :license: GNU GPL.
"""
import locale
from cPickle import dumps
from datetime import datetime
from itertools import chain

from textpress import __version__
from textpress.api import *
from textpress.models import Post, User
try:
    from textpress.utils import build_tag_uri
    from textpress.utils.xml import get_etree, escape
except ImportError:
    # Older Textpress
    from textpress.utils import get_etree, escape, build_tag_uri



ATOM_NS = 'http://www.w3.org/2005/Atom'
TEXTPRESS_NS = 'http://textpress.pocoo.org/'
TEXTPRESS_TAG_URI = TEXTPRESS_NS + '#tag-scheme'
TEXTPRESS_CATEGORY_URI = TEXTPRESS_NS + '#category-scheme'


XML_PREAMBLE = u'''\
<?xml version="1.0" encoding="utf-8"?>
<!--

    This is a TextPress eXtended Atom file.  It is a superset of the Atom
    specification so every Atom-enabled application should be able to use at
    least the Atom subset of the exported data.  You can use this file to
    import your blog data in other blog software.

    Developer Notice
    ~~~~~~~~~~~~~~~~

    Because we saw horrible export formats (wordpress' wxr *cough*) we want
    to avoid problems with this file right away.  If you are an application
    developer that wants to use this file to import blog posts you have to
    to the following:

    -   parse the file with a proper XML parser.  And proper means shout
        on syntax and encoding errors.
    -   handle namespaces!  The prefixes might and will change, so use the
        goddamn full qualified names with the namespaces when parsing.

    User Notice
    ~~~~~~~~~~~

    This file contains a dump of your blog but probably exluding some details
    if plugins did not provide ways to export the information.  It's not
    intended as blog backup nor as preferred solution to migrate from one
    machine to another.  The main purpose of this file is being a portable
    file that can be read by other blog software if you want to switch to
    something else.

-->
<a:feed xmlns:a="%(atom_ns)s" xmlns:tp="%(textpress_ns)s">\
<a:title>%(title)s</a:title>\
<a:subtitle>%(subtitle)s</a:subtitle>\
<a:id>%(id)s</a:id>\
<a:generator uri="http://textpress.pocoo.org/"\
 version="%(version)s">TextPress TPXA Export</a:generator>\
<a:link href="%(blog_url)s"/>\
<a:updated>%(updated)s</a:updated>'''
XML_EPILOG = '</a:feed>'

def format_iso8601(obj):
    return obj.strftime('%Y-%m-%dT%H:%M:%SZ')

def export(app):
    """Dump all the application data into an TPXA response."""
    return Response(Writer(app)._generate(), mimetype='application/atom+xml')


class _MinimalO(object):

    def __init__(self):
        self._buffer = []
        self.write = self._buffer.append

    def _get_and_clean(self):
        rv = ''.join(self._buffer)
        del self._buffer[:]
        return rv


class _ElementHelper(object):

    def __init__(self, etree, ns):
        self._etree = etree
        self._ns = ns

    def __getattr__(self, tag):
        return '{%s}%s' % (self._ns, tag)

    def __call__(self, tag, attrib={}, parent=None, **extra):
        tag = getattr(self, tag)
        text = extra.pop('text', None)
        if parent is not None:
           rv = self._etree.SubElement(parent, tag, attrib, **extra)
        else:
            rv = self._etree.Element(tag, attrib, **extra)
        if text is not None:
            rv.text = text
        return rv


class Participant(object):

    def __init__(self, writer):
        self.app = writer.app
        self.etree = writer.etree
        self.writer = writer

    def before_dump(self):
        pass

    def dump_data(self):
        pass

    def process_post(self, node, post):
        pass

    def process_user(self, node, user):
        pass


class Writer(object):

    def __init__(self, app, description_to_category=True,
                 tags_to_categories=False, keep_as_tags=()):
        self.app = app
        self.description_to_category = description_to_category
        self.tags_to_categories = tags_to_categories
        self.keep_as_tags = keep_as_tags
        self.etree = etree = get_etree()
        self.atom = _ElementHelper(etree, ATOM_NS)
        self.tp = _ElementHelper(etree, TEXTPRESS_NS)
        self._dependencies = {}
        self.users = {}
        self.db_users = {}
        self.participants = [x(self) for x in
                             emit_event('get-tpxa-participants') if x]

    def _generate(self):
        now = datetime.utcnow()
        posts = iter(Post.objects.order_by(Post.last_update.desc()))
        if 'pages' in self.app.plugins:
            try:
                from textpress.plugins import pages1 as textpress_pages
                pages = iter(textpress_pages.Page.objects.all())
            except:
                # Last resort
                pages = iter(())

        try:
            first_post = posts.next()
            first_page = pages.next()
            last_update = first_post.last_update
            posts = chain((first_post,), posts)
            pages = chain((first_page,), pages)
        except StopIteration:
            first_post = None
            first_page = None
            last_update = now

        feed_id = build_tag_uri(self.app, last_update, 'tpxa_export', 'full')
        yield (XML_PREAMBLE % {
            'version':      escape(__version__),
            'title':        escape(self.app.cfg['blog_title']),
            'subtitle':     escape(self.app.cfg['blog_tagline']),
            'atom_ns':      ATOM_NS,
            'textpress_ns': TEXTPRESS_NS,
            'id':           escape(feed_id),
            'blog_url':     escape(self.app.cfg['blog_url']),
            'updated':      format_iso8601(last_update)
        }).encode('utf-8')

        ns_map = {ATOM_NS: 'a', TEXTPRESS_NS: 'tp'}
        out = _MinimalO()
        def dump_node(node):
            self.etree.ElementTree(node)._write(out, node, 'utf-8',
                                                dict(ns_map))
            return out._get_and_clean()

        for participant in self.participants:
            participant.setup()

        # dump configuration
        cfg = self.tp('configuration')
        for key, value in self.app.cfg.iteritems():
            self.tp('item', key=key, text=unicode(value), parent=cfg)
        yield dump_node(cfg)

        # allow plugins to dump trees
        for participant in self.participants:
            rv = participant.dump_data()
            if rv is not None:
                yield dump_node(rv)

        # look up all the users and add them as dependencies
        for user in User.objects.all():
            self._register_user(user)

        # dump all the posts
        for post in posts:
            yield dump_node(self._dump_post(post))

        # dump all the pages
        for page in pages:
            yield dump_node(self._dump_page(page))

        # if we have dependencies (very likely) dump them now
        if self._dependencies:
            yield '<tp:dependencies>'
            for node in self._dependencies.itervalues():
                yield dump_node(node)
            yield '</tp:dependencies>'

        yield XML_EPILOG.encode('utf-8')

    def new_dependency(self, tag):
        id = '%x' % (len(self._dependencies) + 1)
        node = self.etree.Element(tag, {self.tp.dependency: id})
        self._dependencies[id] = node
        return node

    def _register_user(self, user):
        rv = self.new_dependency(self.tp.user)
        self.tp('username', text=user.username, parent=rv)
        self.tp('role', text=str(user.role), parent=rv)
        self.tp('pw_hash', text=user.pw_hash.encode('base64'), parent=rv)
        self.tp('display_name', text=user._display_name, parent=rv)
        self.tp('first_name', text=user.first_name, parent=rv)
        self.tp('last_name', text=user.last_name, parent=rv)
        self.tp('description', text=user.description, parent=rv)
        for participant in self.participants:
            participant.process_user(rv, user)
        self.users[user.user_id] = rv
        self.db_users[user.user_id] = user

    def _dump_post(self, post):
        url = url_for(post, _external=True)
        entry = self.atom('entry', {'xml:base': url})
        self.atom('title', text=post.title, type='text', parent=entry)
        self.atom('id', text=post.uid, parent=entry)
        self.atom('updated', text=format_iso8601(post.last_update),
                  parent=entry)
        self.atom('published', text=format_iso8601(post.pub_date),
                  parent=entry)
        self.atom('link', href=url, parent=entry)

        author = self.atom('author', parent=entry)
        author.attrib[self.tp.dependency] = self.users[post.author.user_id] \
                                                .attrib[self.tp.dependency]
        self.atom('name', text=post.author.display_name, parent=author)
        self.atom('email', text=post.author.email, parent=author)

        self.tp('slug', text=post.slug, parent=entry)
        self.tp('id', text=str(post.post_id), parent=entry)
        self.tp('comments_enabled', text=post.comments_enabled
                and 'yes' or 'no', parent=entry)
        self.tp('pings_enabled', text=post.pings_enabled
                and 'yes' or 'no', parent=entry)
        self.tp('status', text=str(post.status), parent=entry)

        self.atom('content', type='html', text=post.body.render(), parent=entry)
        if post.intro:
            self.atom('summary', type='html', text=post.intro.render(),
                      parent=entry)
        self.tp('content_type', text="entry", parent=entry)
        self.tp('data', text=dumps({
            'extra':        post.extra,
            'raw_body':     post.raw_body,
            'raw_intro':    post.raw_intro,
            'parser_data':  post.parser_data
        }, 2).encode('base64'), parent=entry)

        for c in post.comments:
            comment = self.tp('comment', parent=entry)
            self.tp('id', text=str(c.comment_id), parent=comment)
            author = self.tp('author', parent=comment)
            self.tp('name', text=c.author, parent=author)
            self.tp('email', text=c.email, parent=author)
            self.tp('uri', text=c.www, parent=author)
            self.tp('published', text=format_iso8601(c.pub_date),
                    parent=comment)
            self.tp('blocked', text=c.blocked and 'yes' or 'no',
                    parent=comment)
            self.tp('is_pingback', text=c.is_pingback and 'yes' or 'no',
                    parent=comment)
            self.tp('blocked_msg', text=str(c.blocked_msg or ''),
                    parent=comment)
            self.tp('parent', text=c.parent_id is not None and str(c.parent_id)
                    or '', parent=comment)
            self.tp('status', text=str(c.status), parent=comment)
            self.tp('submitter_ip', text=c.submitter_ip or '0.0.0.0',
                    parent=comment)
            self.tp('data', text=dumps({
                'raw_body':     c.raw_body,
                'parser_data':  c.parser_data
            }, 2).encode('base64'), parent=comment)

        for tag in post.tags:
            if ((tag.description and self.description_to_category) \
            or self.tags_to_categories) and tag.slug not in self.keep_as_tags:
                attrib = dict(term=tag.slug, scheme=TEXTPRESS_CATEGORY_URI)
            else:
                attrib = dict(term=tag.slug, scheme=TEXTPRESS_TAG_URI)
            if tag.slug != tag.name:
                attrib['label'] = tag.name
            self.atom('category', attrib=attrib, parent=entry)

        for participant in self.participants:
            participant.process_post(entry, post)
        return entry

    def _dump_page(self, page):
        now = datetime.utcnow()
        # Fake user, at least make sure it's an admin
        user = [u for u in self.db_users if self.db_users[u].is_manager][0]
        url = url_for(page, _external=True)
        entry = self.atom('entry', {'xml:base': url})
        self.atom('title', text=page.title, type='text', parent=entry)
        self.atom('id', text=str(page.page_id), parent=entry)
        self.atom('updated', text=format_iso8601(now),
                  parent=entry)
        self.atom('published', text=format_iso8601(now),
                  parent=entry)
        self.atom('link', href=url, parent=entry)

        author = self.atom('author', parent=entry)
        author.attrib[self.tp.dependency] = self.users[user].attrib[self.tp.dependency]
        self.atom('name', text=self.db_users[user].display_name, parent=author)
        self.atom('email', text=self.db_users[user].email, parent=author)

        self.tp('slug', text=page.key, parent=entry)
        self.tp('id', text=str(page.page_id), parent=entry)

        self.atom('content', type='html', text=page.body.render(), parent=entry)

        self.tp('content_type', text="page", parent=entry)
        self.tp('data', text=dumps({
            'extra':        page.extra,
            'raw_body':     page.raw_body,
            'extra':        page.extra
        }, 2).encode('base64'), parent=entry)

        for participant in self.participants:
            participant.process_post(entry, page)
        return entry

def main():
    from optparse import OptionParser
    from textpress.application import make_textpress

    parser = OptionParser()
    parser.add_option(
        '--instance', '-i', help="Path to Textpress instance folder")
    parser.add_option(
        '--tags-to-categories', '-t', default=False, action='store_true',
        help="Convert all existing tags to categories instead of tags. "
             "(%default)")
    parser.add_option(
        '--with-descriptions-to-categories', '-d', default=False,
        action='store_true',
        help="Convert all tags with descriptions to categories and all others "
             "are kept as tags. (%default)")
    parser.add_option('--keep-as-tag', '-k', action='append',
        help="keep the passed string has a tag no matter if the above flags "
              "are user or not. Pass multiple '--keep-as-tag/-k' for multiple "
              "tags.")

    options, args = parser.parse_args()
    if not options.instance:
        parser.print_help()
        parser.error("you need to pass the path to your instance folder")
    elif options.tags_to_categories and options.with_descriptions_to_categories:
        parser.error("you can only pass one of --tags-to-categories/"
                     "--with-descriptions-to-categories")

    instance_folder = options.instance
    print "Exporting from %s to" % instance_folder,
    application = make_textpress(instance_folder, True)

    title = application.cfg['blog_title']
    export_filename = title and title.replace(' ', '_') + '_export.tpxa' \
                                                        or 'blog_export.tpxa'

    print export_filename
    export_file = open(export_filename, 'w')

    locale.setlocale(locale.LC_ALL, "C")

    exporter = Writer(application, options.with_descriptions_to_categories,
                      options.tags_to_categories, options.keep_as_tag)
    for entry in exporter._generate():
        export_file.write(entry)

if __name__ == '__main__':
    main()

