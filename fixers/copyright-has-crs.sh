#!/bin/sh
sed -i 's/\r//g' debian/copyright
eco "Remove CRs from copyright file."
