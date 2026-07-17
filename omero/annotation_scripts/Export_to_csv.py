# coding=utf-8
"""
 Export_to_csv.py

 Reads the metadata associated with the images in a dataset
 and creates a csv file attached to dataset

-----------------------------------------------------------------------------
  Copyright (C) 2018 - 2024
  This program is free software; you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation; either version 2 of the License, or
  (at your option) any later version.
  This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.
  You should have received a copy of the GNU General Public License along
  with this program; if not, write to the Free Software Foundation, Inc.,
  51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
------------------------------------------------------------------------------
Created by Christian Evenhuis

"""

import omero
from omero.gateway import BlitzGateway
from omero.rtypes import rstring, rlong, robject
from omero.constants.metadata import NSCLIENTMAPANNOTATION, NSINSIGHTTAGSET
import omero.scripts as scripts

import tempfile
import os
import re
import csv
from collections import OrderedDict, defaultdict

CHILD_OBJECTS = {
    "Project": "Dataset",
    "Dataset": "Image",
    "Screen": "Plate",
    "Plate": "Well",
    "Well": "WellSample",
    "WellSample": "Image"
}

ALLOWED_PARAM = {
    "Project": ["Project", "Dataset", "Image"],
    "Dataset": ["Dataset", "Image"],
    "Image": ["Image"],
    "Screen": ["Screen", "Plate", "Well", "Acquisition", "Image"],
    "Plate": ["Plate", "Well", "Acquisition", "Image"],
    "Well": ["Well", "Image"],
    "Acquisition": ["Acquisition", "Image"],
    "Tag": ["Project", "Dataset", "Image",
            "Screen", "Plate", "Well", "Acquisition"]
}

P_DTYPE = "Data_Type"  # Do not change
P_IDS = "IDs"  # Do not change
P_TARG_DTYPE = "Target Data_Type"
P_NAMESPACE = "Namespace(s) (blank for default)"
P_CSVSEP = "CSV separator"
P_INCL_PARENT = "Include parent container names"
P_INCL_NS = "Include Namespace"
P_INCL_TAG = "Include Tags"

# Add your OMERO.web URL for direct download from link:
# eg https://omero-adress.org/webclient
WEBCLIENT_URL = ""


def get_obj_name(omero_obj):
    """ Helper function """
    if omero_obj.OMERO_CLASS == "Well":
        return omero_obj.getWellPos().upper()
    else:
        return omero_obj.getName()


def get_children_recursive(source_object, target_type):
    """
    Recursively retrieve child objects of a specified type from a source
    OMERO object.

    :param source_object: The OMERO source object from which child objects
    are retrieved.
    :type source_object: omero.model.<ObjectType>
    :param target_type: The OMERO object type to be retrieved as children.
    :type target_type: str
    :return: A list of child objects of the specified target type.
    :rtype: list
    """
    if CHILD_OBJECTS[source_object.OMERO_CLASS] == target_type:
        # Stop condition, we return the source_obj children
        if source_object.OMERO_CLASS != "WellSample":
            return source_object.listChildren()
        else:
            return [source_object.getImage()]
    else:  # Not yet the target
        result = []
        for child_obj in source_object.listChildren():
            # Going down in the Hierarchy list
            result.extend(get_children_recursive(child_obj, target_type))
        return result


def target_iterator(conn, source_object, target_type, is_tag):
    """
    Iterate over and yield target objects of a specified type from a source
    OMERO object.

    :param conn: OMERO connection for server interaction.
    :type conn: omero.gateway.BlitzGateway
    :param source_object: Source OMERO object to iterate over.
    :type source_object: omero.model.<ObjectType>
    :param target_type: Target object type to retrieve.
    :type target_type: str
    :param is_tag: Flag indicating if the source object is a tag.
    :type is_tag: bool
    :yield: Target objects of the specified type.
    :rtype: omero.model.<ObjectType>
    """
    if target_type == source_object.OMERO_CLASS:
        target_obj_l = [source_object]
    elif source_object.OMERO_CLASS == "PlateAcquisition":
        # Check if there is more than one Run, otherwise
        # it's equivalent to start from a plate (and faster this way)
        plate_o = source_object.getParent()
        wellsamp_l = get_children_recursive(plate_o, "WellSample")
        if len(list(plate_o.listPlateAcquisitions())) > 1:
            # Only case where we need to filter on PlateAcquisition
            run_id = source_object.getId()
            wellsamp_l = filter(lambda x: x._obj.plateAcquisition._id._val
                                == run_id, wellsamp_l)
        target_obj_l = [wellsamp.getImage() for wellsamp in wellsamp_l]
    elif target_type == "PlateAcquisition":
        # No direct children access from a plate
        if source_object.OMERO_CLASS == "Screen":
            plate_l = get_children_recursive(source_object, "Plate")
        elif source_object.OMERO_CLASS == "Plate":
            plate_l = [source_object]
        target_obj_l = [r for p in plate_l for r in p.listPlateAcquisitions()]
    elif is_tag:
        target_obj_l = conn.getObjectsByAnnotations(target_type,
                                                    [source_object.getId()])
        # Need that to load objects
        obj_ids = [o.getId() for o in target_obj_l]
        if len(obj_ids) > 0:
            target_obj_l = list(conn.getObjects(target_type, obj_ids))
        else:
            target_obj_l = []
    else:
        target_obj_l = get_children_recursive(source_object,
                                              target_type)

    print(f"Iterating objects from {source_object}:")
    for target_obj in target_obj_l:
        print(f"\t- {target_obj}")
        yield target_obj


def main_loop(conn, script_params):
    """
    Main loop to process each object, gathering annotations, ancestry,
    and writing to a single CSV file.

    Final steps:
    - Combine all annotations into a single structure.
    - Sort rows for better organization.
    - Write the collected data to a single CSV file.

    :param conn: OMERO connection for server interaction.
    :type conn: omero.gateway.BlitzGateway
    :param script_params: Dictionary of script parameters including data types,
        IDs, namespaces, and flags for options like including ancestry and
        tags.
    :type script_params: dict
    :return: Message regarding CSV attachment status, file annotation, and
        result object.
    :rtype: tuple
    """
    source_type = script_params[P_DTYPE]
    target_type = script_params[P_TARG_DTYPE]
    source_ids = script_params[P_IDS]
    namespace_l = script_params[P_NAMESPACE]
    separator = script_params[P_CSVSEP]
    include_parent = script_params[P_INCL_PARENT]
    include_namespace = script_params[P_INCL_NS]
    include_tags = script_params[P_INCL_TAG]

    # One file output per given ID
    obj_ancestry_l = []
    annotations_d = defaultdict(list)
    if include_tags:
        all_tag_d = get_all_tags(conn)
    obj_id_l, obj_name_l, tagannotation_l = [], [], []
    for source_object in conn.getObjects(source_type, source_ids):

        result_obj = source_object
        if source_type == "TagAnnotation":
            result_obj = None  # Attach result csv on the first object
        is_tag = source_type == "TagAnnotation"

        for target_obj in target_iterator(conn, source_object,
                                          target_type, is_tag):
            annotations_d[0].append([])  # (when no ns exported, all ann in 0)
            for ns in namespace_l:
                next_ann_l = get_existing_map_annotations(target_obj,
                                                          ns)
                if ns != "*":
                    annotations_d[ns].append(next_ann_l)
                annotations_d[0][-1].extend(next_ann_l)

            if include_tags:
                tagannotation_l.append(get_existing_tag_annotations(target_obj,
                                                                    all_tag_d))

            obj_id_l.append(target_obj.getId())
            obj_name_l.append(get_obj_name(target_obj))
            if include_parent:
                ancestry = []
                for o in target_obj.getAncestry():
                    if o.OMERO_CLASS == "WellSample":
                        if not o.getPlateAcquisition():
                            continue
                        o = o.getPlateAcquisition()
                    ancestry.append((o.OMERO_CLASS, get_obj_name(o)))
                obj_ancestry_l.append(ancestry[::-1])

            if result_obj is None:
                result_obj = target_obj
        print("\n------------------------------------\n")

    csv_name = f"{get_obj_name(source_object)}_{target_type}-KeyValue.csv"

    if include_namespace and "*" in namespace_l:
        # Assign entries of * namespace
        ns_set = set()
        for ann_l in annotations_d[0]:
            ns_set = ns_set.union([ann.getNs() for ann in ann_l])
        for ann_l in annotations_d[0]:
            for ns in ns_set:
                annotations_d[ns].append([])
            for ann in ann_l:
                annotations_d[ann.getNs()][-1].append(ann)

    # Complete ancestry for image/dataset/plate without parents
    norm_ancestry_l = []
    if len(obj_ancestry_l) > 0:
        # Issue with image that don't have a plateacquisition
        # if combined with images that have
        max_level = max(map(lambda x: len(x), obj_ancestry_l))
        for ancestry in obj_ancestry_l:
            norm_ancestry_l.append([("", "")] *
                                   (max_level - len(ancestry))
                                   + ancestry)

    ns_row, header_row, rows = build_rows(annotations_d, tagannotation_l,
                                          include_namespace)
    ns_row, header_row, rows = sort_concat_rows(ns_row, header_row, rows,
                                                obj_id_l, obj_name_l,
                                                norm_ancestry_l)
    rows.insert(0, header_row)
    if include_namespace:
        rows.insert(0, ns_row)
    file_ann = attach_csv(conn, result_obj, rows, separator, csv_name)

    if file_ann is None:
        message = "The CSV is printed in output, no file could be attached:"
    else:
        message = ("The CSV is attached to " +
                   f"{result_obj.OMERO_CLASS}:{result_obj.getId()}.")

    return message, file_ann, result_obj


def get_all_tags(conn):
    """
    Retrieves all Tag annotations and Tagsets from OMERO.

    :param conn: OMERO connection for server interaction.
    :type conn: omero.gateway.BlitzGateway
    :return: Dictionary mapping tag IDs to Tag names or Tagset names.
    :rtype: dict
    """
    all_tag_d = {}
    for tag in conn.getObjects("TagAnnotation"):

        tagname = tag.getValue()
        if (tag.getNs() == NSINSIGHTTAGSET):
            # It's a tagset, set all tag_id to "tagname[tagset_name]"
            for lk in conn.getAnnotationLinks("TagAnnotation",
                                              parent_ids=[tag.id]):
                child_id = int(lk.child.id.val)
                child_name = lk.child.textValue.val
                all_tag_d[child_id] = f"{child_name}[{tagname}]"
        elif tag.id not in all_tag_d.keys():
            # Normal tag and not in the dict yet
            # (if found as part of a tagset, it is not overwritten)
            all_tag_d[int(tag.id)] = tagname

    return all_tag_d


def get_existing_map_annotations(obj, namespace):
    """
    Retrieves existing Key-Value pair annotations from an OMERO object.

    :param obj: OMERO object to retrieve annotations from.
    :type obj: omero.model.<ObjectType>
    :param namespace: Specific Namespace of annotations to retrieve; '*'
        retrieves all.
    :type namespace: str
    :return: List of MapAnnotationWrapper objects for the specified Namespace.
    :rtype: list
    """
    annotation_l = []
    p = {} if namespace == "*" else {"ns": namespace}
    for ann in obj.listAnnotations(**p):
        if isinstance(ann, omero.gateway.MapAnnotationWrapper):
            annotation_l.append(ann)
    return annotation_l


def get_existing_tag_annotations(obj, all_tag_d):
    """
    Retrieves existing Tag annotations from an OMERO object.

    :param obj: OMERO object to retrieve Tags from.
    :type obj: omero.model.<ObjectType>
    :param all_tag_d: Dictionary of all Tags with Tagset names, if applicable.
    :type all_tag_d: dict
    :return: List of Tag names associated with the specified OMERO object.
    :rtype: list
    """
    annotation_l = []
    for ann in obj.listAnnotations():
        if (isinstance(ann, omero.gateway.TagAnnotationWrapper)
           and ann.getId() in all_tag_d.keys()):
            annotation_l.append(all_tag_d[ann.getId()])
    return annotation_l


def build_rows(annotation_dict_l, tagannotation_l, include_namespace):
    """
    Constructs rows for CSV export by organizing annotations and Tags.

    :param annotation_dict_l: Dictionary of annotations organized by Namespace.
    :type annotation_dict_l: defaultdict(list)
    :param tagannotation_l: List of Tag annotations.
    :type tagannotation_l: list
    :param include_namespace: Flag indicating if Namespace should be included
        in the CSV.
    :type include_namespace: bool
    :return: Tuple containing Namespace row, header row, and data rows for
        the CSV.
    :rtype: tuple
    """
    ns_row = []
    if include_namespace:
        header_row, rows = [], [[] for i in range(len(annotation_dict_l[0]))]
        for ns, annotation_l in annotation_dict_l.items():
            if ns == 0:
                continue
            next_header, next_rows = group_keyvalues(annotation_l)
            ns_row.extend([ns]*len(next_header))
            header_row.extend(next_header)
            for i, next_row in enumerate(next_rows):
                rows[i].extend(next_row)
    else:
        header_row, rows = group_keyvalues(annotation_dict_l[0])

    if len(tagannotation_l) > 0:
        max_tag = max(map(len, tagannotation_l))
        if include_namespace:
            ns_row.extend([""] * max_tag)
        header_row.extend(["TAG"] * max_tag)
        for i, tag_l in enumerate(tagannotation_l):
            rows[i].extend(tag_l)
            rows[i].extend([""] * (max_tag - len(tag_l)))

    return ns_row, header_row, rows


def group_keyvalues(objannotation_l):
    """
    Groups Key-Value pairs of each object into a unified structure for
    CSV export.

    :param objannotation_l: List of object annotations to be grouped.
    :type objannotation_l: list
    :return: Tuple containing the header row and data rows for each object's
        annotations.
    :rtype: tuple
    """
    header_row = OrderedDict()  # To keep the keys in order
    keyval_obj_l = []
    for ann_l in objannotation_l:
        count_k_l = []
        keyval_obj_l.append({})
        for ann in ann_l:
            for (k, v) in ann.getValue():
                n_occurence = count_k_l.count(k)
                pad_k = f"{n_occurence}#{k}"
                keyval_obj_l[-1][pad_k] = v
                header_row[pad_k] = None
                count_k_l.append(k)
    header_row = list(header_row.keys())
    # TODO find how to sort columns when multiple exist
    # or similar

    rows = []
    for keyval_obj in keyval_obj_l:
        obj_dict = OrderedDict((k, "") for k in header_row)
        obj_dict.update(keyval_obj)
        rows.append(list(obj_dict.values()))

    # Removing temporary padding
    header_row = [k[k.find("#")+1:] for k in header_row]
    return header_row, rows


def sort_concat_rows(ns_row, header_row, rows, obj_id_l,
                     obj_name_l, obj_ancestry_l):
    """
    Sorts and concatenates rows, including object IDs, names, and ancestry
    if applicable.

    :param ns_row: Namespace row for CSV.
    :type ns_row: list
    :param header_row: Column headers for CSV.
    :type header_row: list
    :param rows: Data rows for CSV.
    :type rows: list
    :param obj_id_l: List of object IDs.
    :type obj_id_l: list
    :param obj_name_l: List of object names.
    :type obj_name_l: list
    :param obj_ancestry_l: List of ancestry details for each object.
    :type obj_ancestry_l: list
    :return: Tuple containing updated Namespace row, header row, and sorted
        data rows.
    :rtype: tuple
    """
    def convert(text):
        return int(text) if text.isdigit() else text.lower()

    def alphanum_key(key):
        return [convert(c) for c in re.split('([0-9]+)', key)]

    def natural_sort(names):
        # kudos to https://stackoverflow.com/a/4836734/10712860
        names = list(map(alphanum_key, names))
        return sorted(range(len(names)), key=names.__getitem__)

    with_parents = len(obj_ancestry_l) > 0

    prefixes = [""] * len(obj_name_l)
    if with_parents:
        for i in range(len(obj_ancestry_l[0])):
            curr_name_list = [prf+names[i][1] for prf, names
                              in zip(prefixes, obj_ancestry_l)]
            curr_name_set = list(set(curr_name_list))
            indexes = natural_sort(curr_name_set)
            prefix_d = {curr_name_set[idx]: j for j, idx in enumerate(indexes)}
            prefixes = [f"{prefix_d[name]}_" for name in curr_name_list]
    curr_name_list = [prf+name for prf, name in zip(prefixes, obj_name_l)]
    indexes = natural_sort(curr_name_list)

    # End sorting, start concatenation

    res_rows = []
    for idx in indexes:
        curr_row = [str(obj_id_l[idx])] + [obj_name_l[idx]] + rows[idx]
        if with_parents:
            curr_row = [e[1] for e in obj_ancestry_l[idx]] + curr_row
        res_rows.append(curr_row)
    header_row.insert(0, "OBJECT_ID")
    header_row.insert(1, "OBJECT_NAME")
    ns_row.insert(0, "")
    ns_row.insert(1, "")

    if with_parents:
        i = 0
        while "" in [e[0] for e in obj_ancestry_l[i]]:
            i += 1  # Find the row with complete parent names
        for j in range(len(obj_ancestry_l[i])):
            header_row.insert(j, obj_ancestry_l[i][j][0].upper())
            ns_row.insert(j, "")
    ns_row[0] = "NAMESPACE"

    print(f"\tColumn names: {header_row}", "\n")

    return ns_row, header_row, res_rows


def attach_csv(conn, obj_, rows, separator, csv_name):
    """
    Attaches a generated CSV file to an OMERO object.

    :param conn: OMERO connection for server interaction.
    :type conn: omero.gateway.BlitzGateway
    :param obj_: OMERO object to which the CSV file will be attached.
    :type obj_: omero.model.<ObjectType>
    :param rows: Data rows to write into the CSV.
    :type rows: list
    :param separator: Separator character for CSV file.
    :type separator: str
    :param csv_name: Name for the generated CSV file.
    :type csv_name: str
    :return: File annotation object if the file is attached, None otherwise.
    :rtype: omero.model.FileAnnotation
    """
    if not obj_.canAnnotate() and WEBCLIENT_URL == "":
        for row in rows:
            print(f"{separator.join(row)}")
        return None

    # create the tmp directory
    tmp_dir = tempfile.mkdtemp(prefix='MIF_meta')
    (fd, tmp_file) = tempfile.mkstemp(dir=tmp_dir, text=True)
    with os.fdopen(fd, 'w', encoding="utf-8") as tfile:
        tfile.write(f"sep={separator}\n")  # Indicates separator for excel
        csvwriter = csv.writer(tfile,
                               delimiter=separator,
                               quotechar='"',
                               quoting=csv.QUOTE_MINIMAL,
                               lineterminator="\n")
        for row in rows:
            csvwriter.writerow(row)

    # link it to the object
    file_ann = conn.createFileAnnfromLocalFile(
        tmp_file, origFilePathAndName=csv_name,
        ns='KeyVal_export')

    if obj_.canAnnotate():
        obj_.linkAnnotation(file_ann)
        print(f"{file_ann} linked to {obj_}")

    # remove the tmp file
    os.remove(tmp_file)
    os.rmdir(tmp_dir)

    return file_ann.getFile()


def run_script():
    """
    Entry point for the script, called by the client.

    Sets up and executes the main loop based on user-defined parameters,
    and generates output message or URL for the CSV file download.

    :return: Sets output messages and result objects for OMERO client session.
    :rtype: None
    """

    # Cannot add fancy layout if we want auto fill and selct of object ID
    source_types = [
                    rstring("Project"), rstring("Dataset"), rstring("Image"),
                    rstring("Screen"), rstring("Plate"), rstring("Well"),
                    rstring("Acquisition"), rstring("Image"), rstring("Tag"),
    ]

    # Duplicate Image for UI, but not a problem for script
    target_types = [
                    rstring("<selected>"), rstring("Project"),
                    rstring("- Dataset"), rstring("-- Image"),
                    rstring("Screen"), rstring("- Plate"),
                    rstring("-- Well"), rstring("-- Acquisition"),
                    rstring("--- Image"), rstring("<all (from selected)>")
    ]

    separators = [";", ",", "TAB", "|"]
    # Here we define the script name and description.
    # Good practice to put url here to give users more guidance on how to run
    # your script.
    client = scripts.client(
        'Export to CSV',
        """
    Exports in a CSV the Key-Value pairs, Tags, name and ID
    of the selected objects.
    \t
    Check the guide for more information on parameters and errors:
    https://omero-guides.readthedocs.io/en/latest/scripts/docs/annotation_scripts.html
    \t
    Default namespace: openmicroscopy.org/omero/client/mapAnnotation
        """,  # Tabs are needed to add line breaks in the HTML

        scripts.String(
            P_DTYPE, optional=False, grouping="1",
            description="Data type of the parent objects.",
            values=source_types, default="Dataset"),

        scripts.List(
            P_IDS, optional=False, grouping="1.1",
            description="IDs of the parent objects").ofType(rlong(0)),

        scripts.String(
            P_TARG_DTYPE, optional=False, grouping="1.2",
            description="Data type to process from the selected " +
            "parent objects.",
            values=target_types, default="<selected>"),

        scripts.List(
            P_NAMESPACE, optional=True,
            grouping="1.3",
            description="Namespace(s) of the Key-Value pairs " +
                        "to export. Client namespace by default, " +
                        "'*' for all.").ofType(rstring("")),

        scripts.Bool(
            "Other parameters", optional=True, grouping="2", default=True,
            description="Ticking or unticking this has no effect"),

        scripts.String(
            P_CSVSEP, optional=False, grouping="2.1",
            description="Choose the CSV separator.",
            values=separators, default="TAB"),

        scripts.Bool(
            P_INCL_PARENT, optional=True,
            grouping="2.2",
            description="Check to include columns for the parent " +
                        "containers names", default=False),

        scripts.Bool(
            P_INCL_NS, optional=True,
            grouping="2.3",
            description="Check to include the Namespaces " +
                        "of the Key-Value pairs in the CSV.", default=False),

        scripts.Bool(
            P_INCL_TAG, optional=True,
            grouping="2.4",
            description="Check to include Tags in the CSV file.",
            default=False),

        authors=["Christian Evenhuis", "MIF", "Tom Boissonnet"],
        institutions=["University of Technology Sydney", "CAi HHU"],
        contact="https://forum.image.sc/tag/omero",
        version="2.0.0",
    )
    try:
        params = parameters_parsing(client)

        # wrap client to use the Blitz Gateway
        conn = BlitzGateway(client_obj=client)
        messages = []
        targets = params[P_TARG_DTYPE]
        for target in targets:  # Loop on target, use case of process all
            params[P_TARG_DTYPE] = target
            message, fileann, res_obj = main_loop(conn, params)
            messages.append(message)
        client.setOutput("Message", rstring(" ".join(messages)))

        if res_obj is not None and fileann is not None:
            href = f"{WEBCLIENT_URL}/download_original_file/{fileann.getId()}"
            if WEBCLIENT_URL != "":
                url = omero.rtypes.wrap({
                    "type": "URL",
                    "href": href,
                    "title": "CSV file of Key-Value pairs",
                })
                client.setOutput("URL", url)
            else:
                client.setOutput("Result", robject(res_obj._obj))

    except AssertionError as err:
        # Display assertion errors in OMERO.web activities
        client.setOutput("ERROR", rstring(err))
        raise AssertionError(str(err))
    finally:
        client.closeSession()


def parameters_parsing(client):
    """
    Parses and validates input parameters from the client.

    :param client: Script client used to obtain input parameters.
    :type client: omero.scripts.ScriptClient
    :return: Parsed parameters dictionary with defaults for unspecified
        options.
    :rtype: dict
    """
    params = {}
    # Param dict with defaults for optional parameters
    params[P_NAMESPACE] = [NSCLIENTMAPANNOTATION]

    for key in client.getInputKeys():
        if client.getInput(key):
            # unwrap rtypes to String, Integer etc
            params[key] = client.getInput(key, unwrap=True)

    if params[P_TARG_DTYPE] == "<selected>":
        params[P_TARG_DTYPE] = params[P_DTYPE]
    elif params[P_TARG_DTYPE].startswith("-"):
        # Getting rid of the trailing '---' added for the UI
        params[P_TARG_DTYPE] = params[P_TARG_DTYPE].split(" ")[1]

    if params[P_TARG_DTYPE] != "<all (from selected)>":
        assert params[P_TARG_DTYPE] in ALLOWED_PARAM[params[P_DTYPE]], \
               (f"{params['Target Data_Type']} is not a valid target for " +
                f"{params['Data_Type']}.")

    if params[P_TARG_DTYPE] == "<all (from selected)>":
        params[P_TARG_DTYPE] = ALLOWED_PARAM[params[P_DTYPE]]
    else:
        # Convert to list for iteration over single element
        params[P_TARG_DTYPE] = [params[P_TARG_DTYPE]]
    params[P_TARG_DTYPE] = ["PlateAcquisition" if el == "Acquisition" else el
                            for el in params[P_TARG_DTYPE]]

    # Remove duplicate entries from namespace list
    tmp = params[P_NAMESPACE]
    if "*" in tmp:
        tmp = ["*"]
    params[P_NAMESPACE] = list(set(tmp))

    if params[P_DTYPE] == "Tag":
        params[P_DTYPE] = "TagAnnotation"

    print("Input parameters:")
    keys = [P_DTYPE, P_IDS, P_TARG_DTYPE, P_NAMESPACE,
            P_CSVSEP, P_INCL_PARENT, P_INCL_NS, P_INCL_TAG]
    for k in keys:
        print(f"\t- {k}: {params[k]}")
    print("\n####################################\n")

    if params[P_CSVSEP] == "TAB":
        params[P_CSVSEP] = "\t"

    return params


if __name__ == "__main__":
    run_script()
