# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import datetime
import logging
import os
import re
import logging
try:
    import docutils
    import docutils.core
    import docutils.io
    import docutils.readers.standalone
    import docutils.parsers.rst
    #from docutils.writers.html4css1 import HTMLTranslator

    import rst2html5
    from genshi.builder import tag
    from genshi.output import XHTMLSerializer

    # import the directives to have pygments support
    from pelican import rstdirectives  # NOQA
except ImportError:
    core = False
try:
    from markdown import Markdown
except ImportError:
    Markdown = False  # NOQA
try:
    from asciidocapi import AsciiDocAPI
    asciidoc = True
except ImportError:
    asciidoc = False
try:
    from html import escape
except ImportError:
    from cgi import escape
try:
    from html.parser import HTMLParser
except ImportError:
    from HTMLParser import HTMLParser

from pelican.contents import Page, Category, Tag, Author
from pelican.utils import get_date, pelican_open


logger = logging.getLogger(__name__)

METADATA_PROCESSORS = {
    'tags': lambda x, y: [Tag(tag, y) for tag in x.split(',')],
    'date': lambda x, y: get_date(x),
    'status': lambda x, y: x.strip(),
    'category': Category,
    'author': Author,
}

logger = logging.getLogger(__name__)


class Reader(object):
    enabled = True
    file_extensions = ['static']
    extensions = None

    def __init__(self, settings):
        self.settings = settings

    def process_metadata(self, name, value):
        if name in METADATA_PROCESSORS:
            return METADATA_PROCESSORS[name](value, self.settings)
        return value

    def read(self, source_path):
        "No-op parser"
        content = None
        metadata = {}
        return content, metadata


# TODO FIX
class PelicanHTML5Translator(rst2html5.HTML5Translator):

    def visit_abbreviation(self, node):
        attrs = {}
        if node.hasattr('explanation'):
            attrs['title'] = node['explanation']
        self.body.append(self.starttag(node, 'abbr', '', **attrs))

    def depart_abbreviation(self, node):
        self.body.append('</abbr>')


class RstReader(Reader):
    enabled = bool(docutils)
    file_extensions = ['rst']

    def __init__(self, *args, **kwargs):
        super(RstReader, self).__init__(*args, **kwargs)

    def _parse_metadata(self, metadata):
        """Return the dict containing document metadata"""
        return {k.lower(): self.process_metadata(k.lower(), v)
                for k, v in metadata.items()}

    def _get_publisher(self, source_path):
        extra_params = {'initial_header_level': '2',
                        'indent_output': True,
                        'syntax_highlight': 'short',
                        'input_encoding': 'utf-8',
                        'script': None,  # TODO this should not be required. fix the defaulting in rst2html5
                        'traceback': True # TODO remove this when finished
                        }
        user_params = self.settings.get('DOCUTILS_SETTINGS')
        if user_params:
            extra_params.update(user_params)

        pub = docutils.core.Publisher(
            reader=docutils.readers.standalone.Reader(),
            parser=docutils.parsers.rst.Parser(),
            writer=rst2html5.HTML5Writer(),
            destination_class=docutils.io.StringOutput)
        pub.process_programmatic_settings(None, extra_params, None)
        pub.set_source(source_path=source_path)
        pub.publish()
        return pub

    def read(self, source_path):
        """Parses restructured text"""
        pub = self._get_publisher(source_path)
        parts = pub.writer.parts
        content = parts.get('body')

        # warning: hax hax hax hax!
        # TODO somehow remove this hack
        import lxml.etree, lxml.html
        doc = lxml.html.fromstring(content)

        # remove the redundant title tag
        if doc[0].tag == "h1":
            del doc[0]
            content = "".join(lxml.html.tostring(fragment) for fragment in doc)
        # end hax zone.

        metadata = self._parse_metadata(parts['docinfo'])
        metadata.setdefault('title', parts.get('title'))

        return content, metadata


class MarkdownReader(Reader):
    enabled = bool(Markdown)
    file_extensions = ['md', 'markdown', 'mkd', 'mdown']

    def __init__(self, *args, **kwargs):
        super(MarkdownReader, self).__init__(*args, **kwargs)
        self.extensions = self.settings['MD_EXTENSIONS']
        self.extensions.append('meta')
        self._md = Markdown(extensions=self.extensions)

    def _parse_metadata(self, meta):
        """Return the dict containing document metadata"""
        output = {}
        for name, value in meta.items():
            name = name.lower()
            if name == "summary":
                summary_values = "\n".join(value)
                # reset the markdown instance to clear any state
                self._md.reset()
                summary = self._md.convert(summary_values)
                output[name] = self.process_metadata(name, summary)
            else:
                output[name] = self.process_metadata(name, value[0])
        return output

    def read(self, source_path):
        """Parse content and metadata of markdown files"""

        with pelican_open(source_path) as text:
            content = self._md.convert(text)

        metadata = self._parse_metadata(self._md.Meta)
        return content, metadata


class HTMLReader(Reader):
    """Parses HTML files as input, looking for meta, title, and body tags"""
    file_extensions = ['htm', 'html']
    enabled = True

    class _HTMLParser(HTMLParser):
        def __init__(self, settings, filename):
            HTMLParser.__init__(self)
            self.body = ''
            self.metadata = {}
            self.settings = settings

            self._data_buffer = ''

            self._filename = filename

            self._in_top_level = True
            self._in_head = False
            self._in_title = False
            self._in_body = False
            self._in_tags = False

        def handle_starttag(self, tag, attrs):
            if tag == 'head' and self._in_top_level:
                self._in_top_level = False
                self._in_head = True
            elif tag == 'title' and self._in_head:
                self._in_title = True
                self._data_buffer = ''
            elif tag == 'body' and self._in_top_level:
                self._in_top_level = False
                self._in_body = True
                self._data_buffer = ''
            elif tag == 'meta' and self._in_head:
                self._handle_meta_tag(attrs)

            elif self._in_body:
                self._data_buffer += self.build_tag(tag, attrs, False)

        def handle_endtag(self, tag):
            if tag == 'head':
                if self._in_head:
                    self._in_head = False
                    self._in_top_level = True
            elif tag == 'title':
                self._in_title = False
                self.metadata['title'] = self._data_buffer
            elif tag == 'body':
                self.body = self._data_buffer
                self._in_body = False
                self._in_top_level = True
            elif self._in_body:
                self._data_buffer += '</{}>'.format(escape(tag))

        def handle_startendtag(self, tag, attrs):
            if tag == 'meta' and self._in_head:
                self._handle_meta_tag(attrs)
            if self._in_body:
                self._data_buffer += self.build_tag(tag, attrs, True)

        def handle_comment(self, data):
            self._data_buffer += '<!--{}-->'.format(data)

        def handle_data(self, data):
            self._data_buffer += data

        def handle_entityref(self, data):
            self._data_buffer += '&{};'.format(data)

        def handle_charref(self, data):
            self._data_buffer += '&#{};'.format(data)

        def build_tag(self, tag, attrs, close_tag):
            result = '<{}'.format(escape(tag))
            for k, v in attrs:
                result += ' ' + escape(k)
                if v is not None:
                    result += '="{}"'.format(escape(v))
            if close_tag:
                return result + ' />'
            return result + '>'

        def _handle_meta_tag(self, attrs):
            name = self._attr_value(attrs, 'name').lower()
            contents = self._attr_value(attrs, 'content', '')
            if not contents:
                contents = self._attr_value(attrs, 'contents', '')
                if contents:
                    logger.warning("Meta tag attribute 'contents' used in file %s, should be changed to 'content'", self._filename)

            if name == 'keywords':
                name = 'tags'
            self.metadata[name] = contents

        @classmethod
        def _attr_value(cls, attrs, name, default=None):
            return next((x[1] for x in attrs if x[0] == name), default)

    def read(self, filename):
        """Parse content and metadata of HTML files"""
        with pelican_open(filename) as content:
            parser = self._HTMLParser(self.settings, filename)
            parser.feed(content)
            parser.close()

        metadata = {}
        for k in parser.metadata:
            metadata[k] = self.process_metadata(k, parser.metadata[k])
        return parser.body, metadata


class AsciiDocReader(Reader):
    enabled = bool(asciidoc)
    file_extensions = ['asc']
    default_options = ["--no-header-footer", "-a newline=\\n"]

    def read(self, source_path):
        """Parse content and metadata of asciidoc files"""
        from cStringIO import StringIO
        with pelican_open(source_path) as source:
            text = StringIO(source)
        content = StringIO()
        ad = AsciiDocAPI()

        options = self.settings['ASCIIDOC_OPTIONS']
        if isinstance(options, (str, unicode)):
            options = [m.strip() for m in options.split(',')]
        options = self.default_options + options
        for o in options:
            ad.options(*o.split())

        ad.execute(text, content, backend="html4")
        content = content.getvalue()

        metadata = {}
        for name, value in ad.asciidoc.document.attributes.items():
            name = name.lower()
            metadata[name] = self.process_metadata(name, value)
        if 'doctitle' in metadata:
            metadata['title'] = metadata['doctitle']
        return content, metadata


EXTENSIONS = {}

for cls in [Reader] + Reader.__subclasses__():
    for ext in cls.file_extensions:
        EXTENSIONS[ext] = cls


def read_file(base_path, path, content_class=Page, fmt=None,
              settings=None, context=None,
              preread_signal=None, preread_sender=None,
              context_signal=None, context_sender=None):
    """Return a content object parsed with the given format."""
    path = os.path.abspath(os.path.join(base_path, path))
    source_path = os.path.relpath(path, base_path)
    base, ext = os.path.splitext(os.path.basename(path))
    logger.debug('read file {} -> {}'.format(
            source_path, content_class.__name__))
    if not fmt:
        fmt = ext[1:]

    if fmt not in EXTENSIONS:
        raise TypeError('Pelican does not know how to parse {}'.format(path))

    if preread_signal:
        logger.debug('signal {}.send({})'.format(
                preread_signal, preread_sender))
        preread_signal.send(preread_sender)

    if settings is None:
        settings = {}

    reader_class = EXTENSIONS[fmt]
    if not reader_class.enabled:
        raise ValueError('Missing dependencies for {}'.format(fmt))

    reader = reader_class(settings)

    settings_key = '%s_EXTENSIONS' % fmt.upper()

    if settings and settings_key in settings:
        reader.extensions = settings[settings_key]

    metadata = default_metadata(
        settings=settings, process=reader.process_metadata)
    metadata.update(path_metadata(
            full_path=path, source_path=source_path, settings=settings))
    metadata.update(parse_path_metadata(
            source_path=source_path, settings=settings,
            process=reader.process_metadata))
    content, reader_metadata = reader.read(path)
    metadata.update(reader_metadata)

    # eventually filter the content with typogrify if asked so
    if content and settings and settings['TYPOGRIFY']:
        from typogrify.filters import typogrify
        content = typogrify(content)
        metadata['title'] = typogrify(metadata['title'])

    if context_signal:
        logger.debug('signal {}.send({}, <metadata>)'.format(
                context_signal, context_sender))
        context_signal.send(context_sender, metadata=metadata)
    return content_class(
        content=content,
        metadata=metadata,
        settings=settings,
        source_path=path,
        context=context)


def default_metadata(settings=None, process=None):
    metadata = {}
    if settings:
        if 'DEFAULT_CATEGORY' in settings:
            value = settings['DEFAULT_CATEGORY']
            if process:
                value = process('category', value)
            metadata['category'] = value
        if 'DEFAULT_DATE' in settings and settings['DEFAULT_DATE'] != 'fs':
            metadata['date'] = datetime.datetime(*settings['DEFAULT_DATE'])
    return metadata


def path_metadata(full_path, source_path, settings=None):
    metadata = {}
    if settings:
        if settings.get('DEFAULT_DATE', None) == 'fs':
            metadata['date'] = datetime.datetime.fromtimestamp(
                os.stat(full_path).st_ctime)
        metadata.update(settings.get('EXTRA_PATH_METADATA', {}).get(
                source_path, {}))
    return metadata


def parse_path_metadata(source_path, settings=None, process=None):
    """Extract a metadata dictionary from a file's path

    >>> import pprint
    >>> settings = {
    ...     'FILENAME_METADATA': '(?P<slug>[^.]*).*',
    ...     'PATH_METADATA':
    ...         '(?P<category>[^/]*)/(?P<date>\d{4}-\d{2}-\d{2})/.*',
    ...     }
    >>> reader = Reader(settings=settings)
    >>> metadata = parse_path_metadata(
    ...     source_path='my-cat/2013-01-01/my-slug.html',
    ...     settings=settings,
    ...     process=reader.process_metadata)
    >>> pprint.pprint(metadata)  # doctest: +ELLIPSIS
    {'category': <pelican.urlwrappers.Category object at ...>,
     'date': datetime.datetime(2013, 1, 1, 0, 0),
     'slug': 'my-slug'}
    """
    metadata = {}
    dirname, basename = os.path.split(source_path)
    base, ext = os.path.splitext(basename)
    subdir = os.path.basename(dirname)
    if settings:
        checks = []
        for key,data in [('FILENAME_METADATA', base),
                         ('PATH_METADATA', source_path),
                         ]:
            checks.append((settings.get(key, None), data))
        if settings.get('USE_FOLDER_AS_CATEGORY', None):
            checks.insert(0, ('(?P<category>.*)', subdir))
        for regexp,data in checks:
            if regexp and data:
                match = re.match(regexp, data)
                if match:
                    # .items() for py3k compat.
                    for k, v in match.groupdict().items():
                        if k not in metadata:
                            k = k.lower()  # metadata must be lowercase
                            if process:
                                v = process(k, v)
                            metadata[k] = v
    return metadata
