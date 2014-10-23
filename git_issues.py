#!/usr/bin/env python
# coding: utf-8

# git-issue, version 0.3
#
# by John Wiegley <johnw@newartisans.com>

# TODO: (until I can add these bugs to the repo itself!)
#
# 1. use utf-8 throughout
# 2. use -z flag for ls-tree
# 3. use UTC throughout

import datetime
import logging
import optparse
import os
import os.path
import platform
import re
import sys
import tempfile
import xml.dom.minidom

import cPickle as pickle
import gitshelve

logging.basicConfig(level=logging.DEBUG)


if platform.system() == "Windows":
    resolvedLink = None
else:
    try:
        resolvedLink = os.readlink(__file__)
    except:
        resolvedLink = None

    if resolvedLink and resolvedLink[0] != "/":
        resolvedLink = os.path.join(os.path.dirname(__file__), resolvedLink)
    if resolvedLink:
        # print "Symlink found, using %s instead" % resolvedLink
        os.execv(resolvedLink, [resolvedLink] + sys.argv[1:])

path = os.getcwd()

#if ".gitissues" not in __file__:
#    while not exists(os.path.join(path, ".gitissues")):
#        path, extra = split(path)
#        if not extra:
#            break
#    issuesExec = os.path.join(path, ".gitissues/git-issues")
#    if exists(issuesExec):
#        # print "git-issues found in %s. Using it in place of the one
#        # in %s" % (issuesExec, __file__)
#        execv(issuesExec, [issuesExec] + argv[1:])
#        assert ("This should never be called" and False)

try:
    from cStringIO import StringIO
except:
    from StringIO import StringIO


######################################################################

iso_fmt = "%Y%m%dT%H%M%S"
options = None
cache_version = 11

######################################################################


# You may wonder what dirtiness means below.  Here's the deal:
#
# An object is "self-dirty" if it itself has been changed, but possibly none
# of its children.  An object is "dirty" if its children have been changed,
# but possibly not itself.  Any dirtiness is cause for rewriting the object
# cache; only self-dirtiness of specific objects will cause the repository to
# be updated.
class Person(object):
    def __init__(self, name, email):
        self.name = name
        self.email = email

    def __unicode__(self):
        return u"%s <%s>" % (self.name, self.email)

    def __str__(self):
        return unicode(self).encode('utf8')


class Comment(object):
    def __init__(self, issue, author, comment):
        self.name = None
        self.issue = issue
        self.author = author
        self.comment = comment
        self.created = datetime.datetime.now()
        self.modified = None
        self.self_dirty = True
        self.attachments = []  # records filename and blob
        self.issue.comments[self.get_name()] = self  # register into issue

    def mark_dirty(self):
        self.modified = datetime.datetime.now()
        self.self_dirty = True
        self.issue.mark_dirty(self_dirty=False)

    def __getstate__(self):
        odict = self.__dict__.copy()  # copy the dict since we change it
        del odict['self_dirty']  # remove self dirty flag
        return odict

    def __setstate__(self, dict):
        self.__dict__.update(dict)  # update attributes
        self.self_dirty = False


class Issue(object):
    def __init__(self, issueSet, author, title,
                 summary=None,
                 description=None,
                 reporters=[],
                 owners=[],
                 assigned=None,
                 carbons=[],
                 status="new",
                 resolution=None,
                 issue_type="defect",
                 components=[],
                 version=None,
                 milestone=None,
                 severity="major",
                 priority="medium",
                 tags=[]):
        self.issueSet = issueSet
        self.name = None
        self.author = author
        self.title = title
        self.summary = summary
        self.description = description
        self.reporters = reporters
        self.owners = owners
        self.assigned = assigned
        self.carbons = carbons
        self.status = status
        self.resolution = resolution
        self.issue_type = issue_type
        self.components = components
        self.version = version
        self.milestone = milestone
        self.severity = severity
        self.priority = priority
        self.tags = tags
        self.created = datetime.datetime.now()
        self.modified = None
        self.changes = {}
        self.dirty = False
        self.self_dirty = True
        self.comments = {}

        fields = ["author", "title", "summary",
                  "description", "reporters", "owners", "assigned",
                  "carbons", "status", "resolution", "components",
                  "version", "milestone", "severity", "priority",
                  "tags", "modified"]

        for field in fields:
            method = self.__generate_setter(field)
            setattr(self, "set_" + field, method)

    def __generate_setter(self, field):
        def method(self, value):
            logging.debug('self = %s', self)
            logging.debug('value = %s', value)
            self.note_change(field, getattr(self, field), value)
            setattr(self, field, value)

        logging.debug('Generating setter for field %s', field)
        return method

    def mark_dirty(self, self_dirty):
        self.dirty = True
        if self_dirty:
            self.self_dirty = True
            self.modified = datetime.datetime.now()
        self.issueSet.mark_dirty(self_dirty=False)

    def get_name(self):
        assert False

    def note_change(self, field, before, after):
        data = self.changes.get(field, [before, None])
        data[1] = after
        self.changes[field] = data

    def set_issue_type(self, issue_type):
        self.note_change('type', self.issue_type, issue_type)
        self.issue_type = issue_type

    def __getstate__(self):
        odict = self.__dict__.copy()  # copy the dict since we change it
        del odict['changes']  # remove change log
        del odict['dirty']  # remove dirty flag
        del odict['self_dirty']  # remove self dirty flag
        return odict

    def __setstate__(self, dict):
        self.__dict__.update(dict)  # update attributes
        self.changes = {}
        self.dirty = False
        self.self_dirty = False


class IssueSet(object):
    """An IssueSet refers to a group of issues.  There is always at least one
    IssueSet that refers to all of the issues which exist in a repository.
    Other IssueSet's can be generated from that one as "views" or queries into
    that data.

    In essence, it contains both a set of Issue's which can be looked up by
    their unique identifier, and also certain global definition, like the
    allowable components, etc."""
    def __init__(self, shelf):
        self.shelf = shelf
        self.statuses = []
        self.resolutions = []
        self.issue_types = []
        self.components = []
        self.versions = []
        self.milestones = []
        self.severities = []
        self.priorities = []
        self.dirty = False
        self.self_dirty = True
        self.cache_version = cache_version
        self.created = datetime.datetime.now()
        self.modified = None

    def mark_dirty(self, self_dirty):
        self.dirty = True
        if self_dirty:
            self.modified = datetime.datetime.now()
            self.self_dirty = True

    def current_author(self):
        assert False

    def allocate_issue(self, title):
        assert False

    def new_issue(self, title):
        issue = self.allocate_issue(title)
        self.add_issue(issue)
        return issue

    def new_comment(self, issue, text):
        comment = self.allocate_comment(issue, text)
        self.add_comment(comment)
        return comment

    def comment_path(self, comment):
        name = comment.issue.get_name()
        return "%s/%s/comment_%s_%s_%s.xml" % (
            name[:2], name[2:], comment.name,
            datetime.datetime.now().isoformat(),
            comment.comment)

    def issue_path(self, issue):
        name = issue.get_name()
        return '%s/%s/issue.xml' % (name[:2], name[2:])

    def add_issue(self, issue):
        self.shelf[self.issue_path(issue)] = issue
        self.mark_dirty(self_dirty=False)

    def add_comment(self, comment):
        self.shelf[self.comment_path(comment)] = comment
        self.mark_dirty(self_dirty=False)

    def get_comment(self, idx_or_partial_hash):
        comment = None
        try:
            idx = int(idx_or_partial_hash) - 1
            comment = self.shelf[self.shelf.keys()[idx]]
        except:
            print [key for key in self.shelf.iterkeys()]

            def getCommentId(x):
                if not 'comment_' in x:
                    return ""
                x = x.split('comment_')[1]
                x = x.split('_')[0]
                return x
            clean = lambda x: getCommentId(x)
            matching = [(clean(key), key) for key in self.shelf.iterkeys()
                        if clean(key).startswith(idx_or_partial_hash) and
                        not "issue.xml" in clean(key)]
            if len(matching) == 0:
                pass
            elif len(matching) == 1:
                comment = self.shelf[matching[0][1]]
            else:
                print ("Ambiguous hash matches:\n" +
                       '\t\n'.join(a[0] for a in matching))
        if not comment:
            raise Exception(
                "There is no issue matching the identifier '%s'.\n" %
                idx_or_partial_hash)
        return comment

    def __getitem__(self, idx_or_partial_hash):
        issue = None
        try:
            idx = int(idx_or_partial_hash) - 1
            issue = self.shelf[self.shelf.keys()[idx]]
        except:
            clean = lambda x: x.replace('issue.xml', '').replace('/', '')
            matching = [(clean(key), key) for key in self.shelf.iterkeys()
                        if clean(key).startswith(idx_or_partial_hash) and
                        not "comment" in clean(key)]
            if len(matching) == 0:
                pass
            elif len(matching) == 1:
                issue = self.shelf[matching[0][1]]
            else:
                print ("Ambiguous hash matches:\n" +
                       '\t\n'.join(a[0] for a in matching))

        if not issue:
            raise Exception(
                "There is no issue matching the identifier '%s'.\n" %
                idx_or_partial_hash)

        return issue

    def __delitem__(self, idx_or_partial_hash):
        del self.shelf[None]  # jww (2008-05-14): NYI
        assert False

    def issues_cache_file(self):
        assert False

    def load_state(self):
        """Given a newly created IssueSet object as a template, see if
        we can restore the cached version of the data from disk, and
        then check whether it's still valid.  This can _greatly_ speed
        up subsequent list and show operations.

        The reason why a newly created template exists is to abstract
        DVCS-specific behavior, such as the location of the cache file.

        Thus, a typical session looks like this:

          issueSet = GitIssueSet()

          if ... looking at issues list is required ...:
              issueSet = issueSet.load_state()
              ... use the issue data ..."""
        cache_file = self.issues_cache_file()
        if os.path.isfile(cache_file):
            fd = open(cache_file, 'rb')
            if options.verbose:
                print "Cache: Loading saved issues data"
            try:
                cachedIssueSet = pickle.load(fd)
            finally:
                fd.close()

            if cachedIssueSet.cache_version == self.cache_version:
                if options.verbose:
                    print "Cache: It is valid and usable"
                return cachedIssueSet

            if options.verbose:
                print "Cache: No longer valid, throwing it away"

        # We can't use or rely on the cache, so read all details from disk and
        # then mark the IssueSet dirty so that it gets saved back again when
        # we exit.
        try:
            return object_from_string(self.shelf['project.xml'])
        except:
            return self

    def save_state(self):
        """Write an IssueSet to disk in object form, for fast loading on
        the next iteration.  This is only done if there are actual
        changes to write."""
        if not self.dirty:
            return

        self.shelf.sync()

        cache_file = self.issues_cache_file()
        cache_file_dir = os.path.dirname(cache_file)

        if not os.path.isdir(cache_file_dir):
            os.makedirs(cache_file_dir)

        fd = open(cache_file, 'wb')
        try:
            pickle.dump(self.issueSet, fd)   # FIXME
        finally:
            fd.close()

        self.dirty = False

######################################################################


def read_object(obj, file_descriptor):
    return XmlReader.read(file_descriptor)


def object_from_string(str):
    return XmlReader.readString(str)


class XmlReader(object):
    @classmethod
    def read(cls, fd):
        doc = xml.dom.minidom.parse(fd)
        data = XmlRipper.rip(doc.firstChild)
        doc.unlink()
        return data

    @classmethod
    def readString(cls, data):
        doc = xml.dom.minidom.parseString(data)
        data = XmlRipper.rip(doc.firstChild)
        doc.unlink()
        return data


class XmlStringRipper(object):
    @classmethod
    def rip(cls, node):
        return node.data[1:-1]


class XmlListRipper(object):
    @classmethod
    def rip(cls, node):
        assert False


class XmlDateTimeRipper(object):
    @classmethod
    def rip(cls, node):
        return datetime.datetime.strptime(node.childNodes[0].data[1:-1],
                                          iso_fmt)


class XmlPersonRipper(object):
    @classmethod
    def rip(cls, node):
        person = Person(node.childNodes[1].childNodes[0].data[1:-1],
                        node.childNodes[3].childNodes[0].data[1:-1])
        return person


class XmlIssueRipper(object):
    @classmethod
    def rip(cls, node):
        created = XmlRipper.rip(node.childNodes[1].childNodes[1])
        author = XmlRipper.rip(node.childNodes[3].childNodes[1])
        title = XmlRipper.rip(node.childNodes[5].firstChild)

        issue = Issue(None, author, title)
        issue.created = created
        issue.dirty = False

        return issue


class XmlIssueSetRipper(object):
    pass


class XmlRipper(object):
    @classmethod
    def rip(cls, node):
        if node.nodeType == xml.dom.minidom.Node.TEXT_NODE:
            return XmlStringRipper.rip(node)
        elif node.nodeName == 'datetime':
            return XmlDateTimeRipper.rip(node)
        elif node.nodeName == 'person':
            return XmlPersonRipper.rip(node)
        elif node.nodeName == 'list':
            return XmlListRipper.rip(node)
        elif node.nodeName == 'issue':
            return XmlIssueRipper.rip(node)
        elif node.nodeName == 'issue-set':
            return XmlIssueSetRipper.rip(node)
        else:
            print node.nodeType
            print node.nodeName
            assert False


######################################################################
def write_object(obj, file_descriptor=sys.stdout):
    XmlWriter.write(XmlBuilder.build(obj), fd=file_descriptor)


def object_to_string(obj):
    buffer = StringIO()
    XmlWriter.write(XmlBuilder.build(obj), fd=buffer)
    return buffer.getvalue()


class XmlWriter(object):
    @classmethod
    def write(cls, doc, no_header=False, fd=sys.stdout):
        if no_header:
            buffer = StringIO()
            buffer.write(doc.toprettyxml(indent="", encoding="utf-8"))
            fd.write(re.sub('^.+\n', '', buffer.getvalue()))
        else:
            fd.write(doc.toprettyxml(indent="", encoding="utf-8"))
        doc.unlink()


class XmlStringBuilder(object):
    @classmethod
    def build(cls, data, node, doc):
        node.appendChild(doc.createTextNode(data))


class XmlListBuilder(object):
    @classmethod
    def build(cls, data, node, doc):
        element = doc.createElement("list")
        for child in data:
            XmlBuilder.build(doc, element, child)
        node.appendChild(element)


class XmlDateTimeBuilder(object):
    @classmethod
    def build(cls, data, node, doc):
        element = doc.createElement("datetime")
        element.appendChild(doc.createTextNode(data.strftime(iso_fmt)))
        node.appendChild(element)


class XmlPersonBuilder(object):
    @classmethod
    def build(cls, data, node, doc):
        person = doc.createElement("person")

        name = doc.createElement("name")
        name.appendChild(doc.createTextNode(data.name))
        person.appendChild(name)

        email = doc.createElement("email")
        email.appendChild(doc.createTextNode(data.email))
        person.appendChild(email)

        node.appendChild(person)


class XmlIssueBuilder(object):
    @classmethod
    def build(cls, issue, node, doc):
        issueNode = doc.createElement("issue")

        subNodeNames = ["created", "author", "title", "summary",
                        "description", "reporters", "owners", "assigned",
                        "carbons", "status", "resolution", "components",
                        "version", "milestone", "severity", "priority",
                        "tags", "modified"]

        for name in subNodeNames:
            subnode = doc.createElement(name)
            subdata = getattr(issue, name)
            XmlBuilder.build(subdata, subnode, doc)
            node.appendChild(subnode)

        issue_type = doc.createElement("type")
        XmlBuilder.build(issue.issue_type, issue_type, doc)
        issueNode.appendChild(issue_type)

        node.appendChild(issueNode)


class XmlCommentBuilder(object):
    @classmethod
    def build(cls, comment, node, doc):
        commentNode = doc.createElement("comment")

        subNodeNames = ["created", "author", "comment"]

        for name in subNodeNames:
            subnode = doc.createElement(name)
            subdata = getattr(comment, name)
            XmlBuilder.build(subdata, subnode, doc)
            commentNode.appendChild(subnode)

        node.appendChild(commentNode)

# class XmlIssueChangesBuilder:
#    def build(cls, data, node, doc):
#        changes = doc.createElement("changes")
#        doc.appendChild(changes)
#
#        for field_name in self.changes.keys():
#            field = doc.createElement("field")
#            field.setAttribute("name", field_name)
#
#            data = self.changes[field_name]
#
#            before = doc.createElement("before")
#            XmlBuilder.build(data[0], before, doc)
#            field.appendChild(before)
#
#            after = doc.createElement("after")
#            XmlBuilder.build(data[1], after, doc)
#            field.appendChild(after)
#
#            changes.appendChild(field)
#
#        node.appendChild(changes)
#
#    build = classmethod(build)


class XmlIssueSetBuilder(object):
    @classmethod
    def build(cls, issueSet, node, doc):
        set = doc.createElement("issue-set")

        subNodeNames = ["created", "statuses", "resolutions", "components",
                        "versions", "milestones", "severities", "priorities",
                        "modified"]

        for name in subNodeNames:
            subnode = doc.createElement(name)
            subdata = getattr(issueSet, name)
            XmlBuilder.build(subdata, subnode, doc)
            node.appendChild(subnode)

        # types is singled out because it changes names
        issue_types = doc.createElement("types")
        XmlBuilder.build(issueSet.issue_types, issue_types, doc)
        set.appendChild(issue_types)

        node.appendChild(set)


class XmlBuilder(object):
    @classmethod
    def build(cls, data, node=None, doc=None):
        if data is None:
            pass
        elif isinstance(data, datetime.datetime):
            assert doc
            XmlDateTimeBuilder.build(data, node, doc)
        elif isinstance(data, Person):
            assert doc
            XmlPersonBuilder.build(data, node, doc)
        elif isinstance(data, list):
            assert doc
            XmlListBuilder.build(data, node, doc)
        elif isinstance(data, str):
            assert doc
            XmlStringBuilder.build(data, node, doc)
        elif isinstance(data, Issue):
            assert not doc
            doc = xml.dom.minidom.Document()
            XmlIssueBuilder.build(data, doc, doc)
        elif isinstance(data, IssueSet):
            assert not doc
            doc = xml.dom.minidom.Document()
            XmlIssueSetBuilder.build(data, doc, doc)
        elif isinstance(data, Comment):
            assert not doc
            doc = xml.dom.minidom.Document()
            XmlCommentBuilder.build(data, doc, doc)
        else:
            print "Unknown type %s" % data
            assert False

        return doc

######################################################################


class GitIssue(Issue):
    def get_name(self):
        if not self.name:
            hash_func = self.issueSet.shelf.hash_blob
            name = hash_func(str(self.created) + str(self.author) +
                             self.title)
            self.name = name
        return self.name


class GitComment(Comment):
    def get_name(self):
        if not self.name:
            hash_func = self.issue.issueSet.shelf.hash_blob
            name = hash_func(str(self.created)
                             + str(self.author)
                             + self.comment)
            self.name = name
        return self.name


class xml_gitbook(gitshelve.gitbook):
    def serialize_data(self, data):
        return object_to_string(data)

    def deserialize_data(self, data):
        return object_from_string(data)


class GitIssueSet(IssueSet):
    """This object implements all the command necessary to interact with Git
    for the purpose of storing and distributing issues."""
    def __init__(self):
        self.GIT_DIR = None
        self.GIT_AUTHOR = None
        IssueSet.__init__(self, gitshelve.open('issues',
                                               book_type=xml_gitbook))

    def git_directory(self):
        if self.GIT_DIR is None:
            self.GIT_DIR = gitshelve.git('rev-parse', '--git-dir')
        return self.GIT_DIR

    def issues_cache_file(self):
        return os.path.join(self.git_directory(), "issues")

    def current_author(self):
        if self.GIT_AUTHOR is None:
            self.GIT_AUTHOR = Person(gitshelve.git('config', 'user.name'),
                                     gitshelve.git('config', 'user.email'))
        return self.GIT_AUTHOR

    def allocate_issue(self, title):
        return GitIssue(self, self.current_author(), title)

    def allocate_comment(self, issue, commentText):
        return GitComment(issue, self.current_author(), commentText)

######################################################################


def format_long_text(text, indent=13):
    if not text:
        return "<none>"

    lines = text.split('\n')

    delim = "\n" + " " * indent
    return delim.join(lines)


def format_people_list(people, indent=13):
    if not people:
        return "<no one yet>"

    delim = ",\n" + " " * indent
    return delim.join(people)


def terminal_width():
    """Return terminal width."""
    width = 0
    try:
        import struct
        import fcntl
        import termios
        s = struct.pack('HHHH', 0, 0, 0, 0)
        x = fcntl.ioctl(1, termios.TIOCGWINSZ, s)
        width = struct.unpack('HHHH', x)[1]
    except:
        pass
    if width <= 0:
        if "COLUMNS" in os.environ:
            width = int(os.getenv("COLUMNS"))
        if width <= 0:
            width = 80
    return width

######################################################################


def inputFromEditor(originalText):
    fd, tempFile = tempfile.mkstemp()
    f = open(tempFile, "w")
    f.write(originalText or "")
    f.close()

    defaultEditor = "vi"
    if platform.system() == "Windows":
        defaultEditor = "notepad"
    if "VISUAL" in os.environ:
        defaultEditor = os.getenv("VISUAL")
    elif "EDITOR" in os.environ:
        defaultEditor = os.getenv("EDITOR")  # FIXME never used
    editCommand = "%s %s" % (defaultEditor, tempFile)
    if os.system(editCommand) != 0:
        os.unlink(tempFile)
        print "Error while executing %s" % editCommand
        sys.exit(1)
    contents = open(tempFile).read()
    os.unlink(tempFile)
    return contents


def main():
    parser = optparse.OptionParser(
        usage="""Usage: git-issues [options] <command> [command-options]

    Commands:
      init        Creates a copy of git-issues repository in .gitissues in the
                  current git repository.
      list        Lists tickets for this repository
      new         Creates a new ticket for this repository
      show/dump   Shows the given ticket
      change      Change options for the given ticket
      edit        edit options for the given ticket in text editor
      comment     Add a comment to the given ticket
      close       Close the given ticket""")
    parser.add_option("-v", "--verbose",
                      action="store_true",
                      dest="verbose",
                      default=False,
                      help="report activity options.verbosely")

    parser.add_option("--print-new-bugs",
                      action="store_true",
                      dest="printNewBugs",
                      default=False,
                      help="prints out a formatted string with the bug " +
                      "summary and id. Usuful for in editor usage.")

    parser.add_option("--filter-status",
                      dest="filterStatus",
                      default="closed",
                      help="do not print a issue if it is in one of\n"
                      + "the stati specified (column separated) by this " +
                      "option.".replace("\n", ""))

    parser.add_option("--filter-tags",
                      dest="filterTags",
                      default="",
                      help="""Prints only the issues with one of the following
    tags (column separated) associated to it.""")

    parser.add_option("--screen-width",
                      dest="screenWidth",
                      default=terminal_width(),
                      help="Width of the terminal we are printing to.")

    parser.add_option("--status",
                      dest="status",
                      default=None,
                      metavar="STATUS",
                      help="Set the status of the issue to STATUS " +
                      "when creating it.")

    (options, args) = parser.parse_args()

    gitshelve.verbose = options.verbose

    if len(args) == 0:
        parser.print_help()
        sys.exit(1)

    command = args[0]
    args = args[1:]

######################################################################

    # jww (2008-05-12): Pick the appropriate IssueSet to use based on the
    # environment.

    issueSet = GitIssueSet().load_state()

######################################################################

    if command == "init":
        #from os.path import split, join, exists
        #from os import makedirs
        path = os.getcwd()
        while not os.path.exists(os.path.join(path, ".git")):
            path, extra = os.path.split(path)
            if not extra:
                print "Unable to find a git repository. "
                print "Make sure you ran `git init` at some point."
                sys.exit(1)
        #issuesdir = os.path.join(path, ".gitissues")
        #if os.path.exists(issuesdir):
        #    print "git-issues helper directory %s already exists." % issuesdir
        #    print "Doing nothing."
        #    sys.exit(1)
        #os.makedirs(issuesdir)
        sys.exit(0)

######################################################################

    elif command == "list":
        header = "   #    Id     Title%sState  Date  Assign  Tags"
        width = int(options.screenWidth)
        titleWidth = width - len(header) + 2
        print header % "".join([" " for x in xrange(titleWidth)])
        print "".join(["-" for x in xrange(width)])

        index = 1
        filteredStati = options.filterStatus.split(":")
        wantedTags = set(options.filterTags.split(":"))

        for item in issueSet.shelf.iteritems():
            if "comment" in item[0]:
                continue
            issue = item[1].get_data()
            if issue.status in filteredStati:
                continue
            if wantedTags and not issue.tags:
                continue
            if wantedTags:
                matchingTags = [
                    tag for tag in issue.tags.split(", ") if tag in wantedTags
                ]
                if not matchingTags:
                    continue
            formatString = "%4d  %s  %-" + \
                str(titleWidth + len("Title") - 1) + "s %-6s %5s %6s %s"
            print formatString % \
                (index, issue.name[:7], issue.title, issue.status,
                 issue.created and issue.created.strftime('%m/%d'),
                 str(issue.author)[:6], '')
            index += 1

        print

######################################################################

    elif command == "show" or command == "dump":
        if len(args) == 0:
            print "Usage: %s %s <issue-id | index>" % (sys.argv[0], command)
        else:
            issue = issueSet[args[0]]
            issue.comments = "\n       ".join(
                ["Comment (%s): %s" % (comment[0:7],
                 issueSet.get_comment(comment[0:7]).comment)
                 for comment in issue.comments])
            if command == "show":
                if issue.title:
                    print "          Title:", issue.title
                if issue.summary:
                    print "        Summary:", format_long_text(issue.summary)
                    print
                if issue.description:
                    print "    Description:", \
                        format_long_text(issue.description)
                    print
                if issue.author:
                    print "         Author:", issue.author
                if issue.reporters:
                    print "    Reporter(s):", \
                        format_people_list(issue.reporters)
                if issue.owners:
                    print "       Owner(s):", format_people_list(issue.owners)
                if issue.assigned:
                    print "       Assigned:", \
                        format_people_list(issue.assigned)
                if issue.carbons:
                    print "             Cc:", format_people_list(issue.carbons)

                if issue.issue_type:
                    print "           Type:", issue.issue_type
                if issue.status:
                    print "         Status:", issue.status
                if issue.resolution:
                    print "     Resolution:", issue.resolution
                if issue.components:
                    print "     Components:", issue.components
                if issue.version:
                    print "        Version:", issue.version
                if issue.milestone:
                    print "      Milestone:", issue.milestone
                if issue.severity:
                    print "       Severity:", issue.severity
                if issue.priority:
                    print "       Priority:", issue.priority
                if issue.tags:
                    print "           Tags:", issue.tags

                print "        Created:", issue.created
                if issue.modified:
                    print "       Modified:", issue.modified
            else:
                write_object(issue)

######################################################################

    elif command == "change":
        if len(args) == 0:
            print "Usage: %s change <issue-id> <field> <value>" % sys.argv[0]
        else:
            issue = issueSet[args[0]]

            # jww (2008-05-13): Need to parse datetime, lists, and people
            method = getattr(issue, "set_" + args[1])
            try:
                method(args[2])
            except IndexError:
                print "Index error."
                print args
                sys.exit(1)
######################################################################

    elif command == "edit":
        if len(args) == 0:
            print "Usage: %s edit <issue-id> <field>" % sys.argv[0]
            sys.exit(1)
        else:
            issue = issueSet[args[0]]
            if len(args) != 2:
                print "Usage: %s edit <issue-id> <field>" % sys.argv[0]
                sys.exit(1)
            if not sys.stdin.isatty():
                contents = sys.stdin.read()
            else:
                cmd = args[1].lower()
                contents = inputFromEditor(getattr(issue, cmd))
            getattr(issue, "set_" + cmd)(contents)

######################################################################

    elif command == "close":
        if len(args) == 0:
            print "Usage: %s close <issue-id>" % os.path.basename(sys.argv[0])
            sys.exit(1)
        issue = issueSet[args[0]]
        issue.set_status("closed")

######################################################################

    elif command == "new":
        if len(args) == 0:
            print "Usage: %s new <title>" % sys.argv[0]
        else:
            issue = issueSet.new_issue(args[0])
            logging.debug('issue = dir %s', dir(issue))
            logging.debug('issue.set_status = dir %s', dir(issue.set_status))
            if options.status:
                issue.set_status(options.status)
            else:
                issue.set_status(issue, "TODO")
            logging.debug('issue = %s', issue.status)

            if options.printNewBugs:
                print "%s: %s (%s)" % \
                    (issue.status, issue.title, issue.name[0:7])
    elif command == "comment":
        if len(args) == 0:
            print "Usage: %s comment <issue-id> <comment-title>" % sys.argv[0]
            sys.exit(1)
        issue = issueSet[args[0]]
        comment = issueSet.new_comment(issue, args[1])
        if options.printNewBugs:
            print "### Comment(%s): %s" % (comment.name[0:7], comment.comment)

######################################################################

    else:
        print "Unknown command %s" % command
    # If any of the commands made the issueSet dirty, (possibly) update the
    # repository and write out a new cache

    issueSet.save_state()


if __name__ == '__main__':
    main()
