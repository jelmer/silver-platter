#!/bin/sh
perl -p -i -e 's/^Homepage: http:\/\/github.com/Homepage: https:\/\/github.com/' debian/control
perl -p -i -e 's/^Homepage: http:\/\/launchpad.net/Homepage: https:\/\/launchpad.net/' debian/control
perl -p -i -e 's/^Homepage: http:\/\/pypi.python.org/Homepage: https:\/\/pypi.python.org/' debian/control
perl -p -i -e 's/^Homepage: http:\/\/pear.php.net/Homepage: https:\/\/pear.php.net/' debian/control
perl -p -i -e 's/^Homepage: http:\/\/pecl.php.net/Homepage: https:\/\/pecl.php.net/' debian/control
perl -p -i -e 's/^Homepage: http:\/\/www.bioconductor.org/Homepage: https:\/\/www.bioconductor.org/' debian/control
perl -p -i -e 's/^Homepage: http:\/\/bioconductor.org/Homepage: https:\/\/bioconductor.org/' debian/control
perl -p -i -e 's/^Homepage: http:\/\/cran.r-project.org/Homepage: https:\/\/cran.r-project.org/' debian/control
perl -p -i -e 's/^Homepage: http:\/\/wiki.debian.org/Homepage: https:\/\/wiki.debian.org/' debian/control
echo "Use secure URI in Homepage field."
