from gofed_lib.logger.logger import Logger

import optparse
from gofed_lib.utils import ENDC, RED, GREEN, runCommand
from gofed.modules.Utils import FormatedPrint

from gofed.modules.SpecGenerator import SpecGenerator

import os
import sys
import errno
import logging
import json

from gofed_infra.system.core.factory.actfactory import ActFactory
from gofed_lib.go.data2specmodeldata import Data2SpecModelData
from gofed_lib.go.contentmetadataextractor import ContentMetadataExtractor
from gofed_lib.go.importpath.parserbuilder import ImportPathParserBuilder
from gofed_lib.go.importpath.decomposerbuilder import ImportPathsDecomposerBuilder
from gofed_lib.types import UnsupportedImportPathError
from gofed_lib.distribution.clients.pkgdb.client import PkgDBClient
from gofed_lib.distribution.packagenamegeneratorbuilder import PackageNameGeneratorBuilder
from gofed_lib.providers.providerbuilder import ProviderBuilder
from gofed_lib.repository.repositoryclientbuilder import RepositoryClientBuilder
from gofed_infra.system.artefacts.artefacts import (
	ARTEFACT_GOLANG_PROJECT_PACKAGES,
	ARTEFACT_GOLANG_PROJECT_CONTENT_METADATA
)

def setOptions():
	parser = optparse.OptionParser("%prog [-e] [-d] file [file [file ...]]")

	sln = not (os.path.basename(sys.argv[0]) == "repo2gospec.py")
	github = os.path.basename(sys.argv[0]) == "github2gospec"
	googlecode = os.path.basename(sys.argv[0]) == "googlecode2gospec"
	bitbucket = os.path.basename(sys.argv[0]) == "bitbucket2gospec"

	SH = optparse.SUPPRESS_HELP

	parser.add_option(
	    "", "", "--github", dest="github", action="store_true", default = False,
	    help = SH if sln else "github.com repository"
	)

	parser.add_option(
	    "", "", "--googlecode", dest="googlecode", action="store_true", default = False,
	    help = SH if sln else "code.google.com repository"
	)

	parser.add_option(
	    "", "", "--bitbucket", dest="bitbucket", action="store_true", default = False,
	    help = SH if sln else "bitbucket.org repository"
	)

	parser.add_option(
	    "", "", "--detect", dest="detect", default = "",
	    help = SH if sln else "Detect repository from import path"
	)

	parser.add_option(
	    "", "", "--skip-errors", dest="skiperrors", action="store_true", default = False,
	    help = SH if sln else "Skip errors during Go symbol parsing"
	)

	if github:
		help_text = "Repository name, github.com/project/REPO"
	elif googlecode:
		help_text = "Repository name, code.google.com/p/REPO"
	elif bitbucket:
		help_text = "Repository name, bitbucket.org/project/REPO"
	else:
		help_text = "Repository name, e.g. github.com/project/REPO"
	

	parser.add_option(
	    "", "-r", "--repo", dest="repo", default = "",
	    help = help_text
	)

	if github:
		help_text = "Repository name, github.com/PROJECT/repository"
	elif bitbucket:
		help_text = "Repository name, bitbucket.org/PROJECT/repository"
	else:
		help_text = "Repository name, e.g. github.com/PROJECT/repository"

	parser.add_option(
	    "", "-p", "--project", dest="project", default = "",
	    help = SH if googlecode else help_text
	)

	if googlecode:
		parser.add_option(
		    "", "-c", "--rev", dest="revision", default = "",
		    help = "Revision"
		)
	else:
		parser.add_option(
		    "", "-c", "--commit", dest="commit", default = "",
		    help = "Commit. If not specified the latest is taken."
		)

	parser.add_option(
	    "", "-f", "--format", dest="format", action="store_true", default = False,
	    help = "Make messages more shiny"
	)

	parser.add_option(
	    "", "", "--force", dest="force", action="store_true", default = False,
	    help = "Generate spec file even if it is already in Fedora"
	)

	parser.add_option(
            "", "", "--with-build", dest="withbuild", action = "store_true", default = False,
            help = "Generate spec file with %build section"
        )

	parser.add_option(
            "", "", "--with-extra", dest="withextra", action = "store_true", default = False,
            help = "Generate spec file with additional pieces (e.g. definition of %gobuild and %gotest for explicit distributions)"
        )

	parser.add_option(
	    "", "-d", "--dir", dest="directory", default = "",
	    help = "Generate spec file from directory"
	)

	parser.add_option(
	    "", "-t", "--target", dest="target", default = "",
	    help = "Target directory to generate spec file to"
	)

	parser.add_option(
            "", "-v", "--verbose", dest="verbose", action = "store_true", default = False,
            help = "Show all packages if -d option is on"
        )

	return parser.parse_args()

def checkOptions(options):
	fail = False

	if options.detect != "":
		return False

	if not options.github and not options.googlecode and not options.bitbucket:
		print "No provider specified"
		fail = True

	if options.github or options.googlecode or options.bitbucket:
		if options.repo == "":
			print "Repository missing"
			fail = True

	if options.github or options.bitbucket:
		if options.project == "":
			print "Project missing"
			fail = True

	if options.googlecode:
		if options.revision == "":
			print "Revision missing"
			fail = True


	return fail

def printBasicInfo(url, commit, name, formated=True):
	fmt_obj = FormatedPrint(formated)
	fmt_obj.printInfo("Repo URL: %s" % url)
	fmt_obj.printInfo("Commit: %s" % commit)
	fmt_obj.printInfo("Name: %s" % name)

# http://stackoverflow.com/questions/273192/in-python-check-if-a-directory-exists-and-create-it-if-necessary
def make_sure_path_exists(path):
    try:
        os.makedirs(path)
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise

def isPkgInPkgDB(name, force):
	if PkgDBClient().packageExists(name):
		print "%sPackage %s already exists%s" % (RED, name, ENDC)
		if not force:
			exit(1)

def createTargetDirectories(name, target = ""):
	if target == "":
		target = "%s/fedora/%s" % (name, name)
	else:
		target = os.path.abspath(target)

	make_sure_path_exists(target)
	os.chdir(target)

def checkDependencies(fmt_obj, classes, url):
	name_generator = PackageNameGeneratorBuilder().buildWithLocalMapping()
	for element in sorted(classes.keys()):
		if element == "Unknown":
			fmt_obj.printWarning("Some import paths were not detected. Please run gofed ggi -c on extracted tarball manually")
			continue

		classes[element] = sorted(classes[element])

		try:
			pkg_name = name_generator.generate(element).name()
		except UnsupportedImportPathError as e:
			fmt_obj.printWarning("Unable to translate %s to package name: %s" % (element, e))
			continue

		pkg_in_pkgdb = PkgDBClient().packageExists(pkg_name)
		if pkg_in_pkgdb:
			print (GREEN + "\tClass: %s (%s) PkgDB=%s" + ENDC) % (element, pkg_name, pkg_in_pkgdb)
		else:
			print (RED + "\tClass: %s (%s) PkgDB=%s" + ENDC ) % (element, pkg_name, pkg_in_pkgdb)

if __name__ == "__main__":

	options, args = setOptions()

	if checkOptions(options):
		exit(1)

	Logger.set(options.verbose)

	fmt_obj = FormatedPrint(options.format)

	if not options.format:
		ENDC = ""
		RED = ""
		GREEN = ""

	if options.detect == "":
		# collect spec file information
		project = options.project
		repo = options.repo
		if not options.googlecode and project == "":
			fmt_obj.printError("Project missing")
			exit(1)

		if repo == "":
			fmt_obj.printError("Repository missing")
			exit(1)

		if options.github:
			import_path = "github.com/%s/%s" % (project, repo)
			commit = options.commit
		elif options.googlecode:
			import_path = "code.google.com/p/%s" % repo
			commit = options.revision
		elif options.bitbucket:
			import_path = "bitbucket.org/%s/%s" % (project, repo)
			commit = options.commit
		else:
			fmt_obj.printError("Provider not supported")
			exit(1)

	else:
		import_path = options.detect
		commit = options.commit

	path = ""
	if options.directory != "":
		if options.directory[0] == "/":
			path = options.directory
		else:
			path = os.path.abspath(options.directory)

		# path exists?
		if not os.path.exists(path):
			logging.error("Path '%s' does not exist" % path)
			exit(1)

	name_generator = PackageNameGeneratorBuilder().buildWithLocalMapping()
	upstream_provider = ProviderBuilder().buildUpstreamWithLocalMapping()
	ipparser = ImportPathParserBuilder().buildWithLocalMapping()

	import_path_prefix = ipparser.parse(import_path).prefix()
	repository_prefix = upstream_provider.parse(import_path_prefix).prefix()
	repository_signature = upstream_provider.signature()
	name = name_generator.generate(import_path_prefix).name()

	# 1. decode some package info (name, archive url, ...)
	# 2. set path to downloaded tarball
	# 3. retrieve project info from tarball
	# 4. generate spec file
	specfile = "%s.spec" % name
	total = 4

	# commit
	if options.directory == "" and commit == "":
		commit = RepositoryClientBuilder().buildWithRemoteClient(repository_signature).latestCommit()["hexsha"]

	# print basic information
	printBasicInfo(repository_prefix, commit, name, options.format)
	print ""

	# is the package already in Fedora
	fmt_obj.printProgress("(1/%s) Checking if the package already exists in PkgDB" % total)
	isPkgInPkgDB(name, options.force)

	# creating target directory structure
	createTargetDirectories(name, options.target)

	# download tarball
	fmt_obj.printProgress("(2/%s) Collecting data" % total)

	# convert import path to project provider path
	metadata = {
		"provider_prefix": repository_prefix,
		"import_path": import_path_prefix,
		"commit": commit,
		"package_name": name
		#{"key": "summary", "value": "..."},
		#{"key": "description", "value": "..."},
		#{"key": "website", "value": "https://godoc.org/github.com/bradfitz/http2"}
	}

	if options.directory != "":
		data = {
			"type": "user_directory",
			"resource": os.path.abspath(path),
			"ipprefix": metadata["import_path"]
		}
	else:
		data = {
			"type": "upstream_source_code",
			"repository": repository_signature,
			"commit": metadata["commit"],
			"ipprefix": metadata["import_path"]
		}

	try:
		data = ActFactory().bake("spec-model-data-provider").call(data)
	except Exception as e:
		logging.error(e)
		exit(1)

	combiner = Data2SpecModelData()
	combiner.combine(
		metadata,
		data[ARTEFACT_GOLANG_PROJECT_PACKAGES],
		data[ARTEFACT_GOLANG_PROJECT_CONTENT_METADATA]
	)
	data = combiner.getData()

	# generate spec file
	fmt_obj.printProgress("(3/%s) Generating spec file" % total)

	spec = SpecGenerator(
		with_build = options.withbuild,
		with_extra = options.withextra
	)

	try:
		with open("%s" % specfile, "w") as f:
			spec.generate(data, f)

	except IOError:
		fmt_obj.printErr("Error: can\'t write to '%s' file" % specfile)
		exit(1)

	so, se, rc = runCommand("rpmdev-bumpspec %s -c \"First package for Fedora\"" % specfile)
	if rc != 0:
		fmt_obj.printErr("Unable to bump spec file: %s" % se)
		exit(1)

	fmt_obj.printProgress("(4/%s) Discovering golang dependencies" % total)

	if data["data"]["packages"] != []:
		package_deps = reduce(lambda x,y: x + y, map(lambda l: l["dependencies"], data["data"]["packages"]))
	else:
		package_deps = []

	if data["data"]["tests"] != []:
		test_deps = reduce(lambda x,y: x + y, map(lambda l: l["dependencies"], data["data"]["tests"]))
	else:
		test_deps = []

	package_deps = sorted(list(set(package_deps)))
	diff_deps = sorted(list(set(test_deps) - set(package_deps)))
	decomposer = ImportPathsDecomposerBuilder().buildLocalDecomposer()

	# filter out self imports
	package_deps = filter(lambda l: not l.startswith(metadata["import_path"]), package_deps)
	diff_deps = filter(lambda l: not l.startswith(metadata["import_path"]), diff_deps)

	add_line = True
	if package_deps != []:
		fmt_obj.printProgress("Discovering package dependencies")
		decomposer.decompose(package_deps)
		classes = decomposer.classes()
		checkDependencies(fmt_obj, classes, metadata["import_path"])
		print ""
		add_line = False

	if diff_deps != []:
		fmt_obj.printProgress("Discovering test dependencies")
		decomposer.decompose(diff_deps)
		classes = decomposer.classes()
		checkDependencies(fmt_obj, classes, metadata["import_path"])
		print ""
		add_line = False

	if add_line:
		print ""

	fmt_obj.printInfo("Spec file %s at %s" % (specfile, os.getcwd()))
