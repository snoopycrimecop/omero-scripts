#!/usr/bin/env python
# -*- coding: utf-8 -*-

#
# Copyright (C) 2016 University of Dundee & Open Microscopy Environment.
# All rights reserved. Use is subject to license terms supplied in LICENSE.txt
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

"""
   Integration test for annotation scripts.
"""

from __future__ import print_function
import omero
from omero.gateway import BlitzGateway
import omero.scripts
import pytest
from script import ScriptTest
from script import run_script
from omero.cmd import Delete2
from omero.rtypes import wrap, rstring, rlist, rbool
from omero.util.temp_files import create_path

import_script = "/omero/annotation_scripts/Import_from_csv.py"
export_script = "/omero/annotation_scripts/Export_to_csv.py"
delete_script = "/omero/annotation_scripts/Remove_KeyVal.py"
convert_script = "/omero/util_scripts/Convert_KeyVal_namespace.py"

DEFAULT_IMPORT_ARGS = {
    "CSV separator": rstring("guess"),
    "Columns to exclude": rlist([
        rstring("<ID>"),
        rstring("<NAME>"),
        rstring("<PARENTS>")
    ]),
    "Target ID colname": rstring("OBJECT_ID"),
    "Target name colname": rstring("OBJECT_NAME"),
    "Exclude empty values": rbool(False),
    "Attach CSV file": rbool(False),
    "Split values on": rstring(""),
    "Import tags": rbool(False),
    "Only use personal tags": rbool(False),
    "Allow tag creation": rbool(False),
}

CLIENT, USER = None, None

def link_file_plate(client, plate, cvs_file):
    conn = BlitzGateway(client_obj=client)
    fa = conn.createFileAnnfromLocalFile(cvs_file, mimetype="text/csv")
    assert fa is not None
    assert fa.id > 0
    link = omero.model.PlateAnnotationLinkI()
    link.setParent(plate)
    link.setChild(omero.model.FileAnnotationI(fa.id, False))
    client.getSession().getUpdateService().saveAndReturnObject(link)
    return fa


class TestAnnotationScripts(ScriptTest):

    def test_import_name(self):
        sid = super(TestAnnotationScripts, self).get_script(import_script)
        assert sid > 0

        client, user = self.new_client_and_user()

        # self.create_test_image(name="testImage", session=self.client.getSession())

        plates = self.import_plates(client, plate_cols=3, plate_rows=1)
        plate = plates[0]

        cvs_file = create_path("test_kvp_name", ".csv")
        # create a file annotation
        with open(cvs_file.abspath(), 'w') as f:
            f.write("OBJECT_NAME,key1\n")
            f.write("A1,value1\n")
            f.write("A2,value2\n")
            f.write("A3,value3\n")

        fa = link_file_plate(client, plate, cvs_file)

        # run the script
        args = DEFAULT_IMPORT_ARGS.copy()
        args["Data_Type"] = rstring("Plate")
        args["IDs"] = rlist([omero.rtypes.rlong(plate.id.val)])
        args["Target Data_Type"] = rstring("-- Well")
        args["File_Annotation"] = rstring(str(fa.id))

        # Making sure tags have no influence here
        for import_tag in [True, False]:
            for pers_tag in [True, False]:
                for tag_creation in [True, False]:
                    args["Import tags"] = rbool(import_tag)
                    args["Only use personal tags"] = rbool(pers_tag)
                    args["Allow tag creation"] = rbool(tag_creation)
                    message = run_script(client, sid, args, "Message")
                    assert message._val == "Added Annotations to 3/3 Well(s)"


    def test_import_id(self):
        pass

    def test_import_innersplit(self):
        pass

    def test_import_separator(self):
        pass

    def test_import_attachfile(self):
        pass

    def test_import_colname(self):
        pass



