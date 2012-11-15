#!/usr/bin/python
# -*- coding: utf-8 -*-

"""
	e3_data_wrangle.py - Script used to produce ...
	Usage:

	Example:
"""

__author__ = "Ryan Faulkner <rfaulkner@wikimedia.org>"
__date__ = "October 3rd, 2012"
__license__ = "GPL (version 2 or later)"

import sys
import settings as s
sys.path.append(s.__E3_Analysis_Home__)

import logging
import argparse
import datetime
from dateutil.parser import parse as date_parse
import src.etl.data_loader as dl
import e3_experiment_definitions as e3_def
import src.metrics.bytes_added as ba
import src.metrics.blocks as b
import src.metrics.time_to_threshold as ttt
import src.etl.log_parser as lp

# CONFIGURE THE LOGGER
logging.basicConfig(level=logging.DEBUG, stream=sys.stderr,
    format='%(asctime)s %(levelname)-8s %(message)s', datefmt='%b-%d %H:%M:%S')

global exp_meta_data
global conn
conn = dl.Connector(instance='slave')

def insert_buckets(l,user_buckets,user_index=0,bucket_index=1):
    """ Insert the user bucket into list of user metrics"""
    for e in l:
        try:
            user = e[user_index]
            e.insert(bucket_index, user_buckets[user])
            yield e
        except IndexError:
            e.remove(b)
        except KeyError:
            e.remove(b)

def load_logs():
    global exp_meta_data

    for key in exp_meta_data['log_data']:
        logging.info('Loading log data for %s...' % key)
        log_data_def = exp_meta_data['log_data'][key]

        conn._cur_.execute('drop table if exists %s' % log_data_def['table_name'])
        conn._cur_.execute(" ".join(log_data_def['definition'].strip().split('\n')))

        for f in exp_meta_data['log_files']:
            logging.info('Processing file %s ...' % f)
            contents = lp.LineParseMethods.parse(f, log_data_def['log_parser_method'])
            dl.DataLoader().create_table_from_list(contents, '',log_data_def['table_name'])

def blocks(users, user_index = 0, bucket_index=1):
    global exp_meta_data
    global conn

    logging.info("Processing blocked users for %s" % str(exp_meta_data['start_date']))
    sql = """
                select
                    user_id,
                    user_name
                from enwiki.user
                where user_id in (%(users)s)
              """ % {'users' : dl.DataLoader().format_comma_separated_list(users.keys(), include_quotes=False)}
    results = conn.execute_SQL(" ".join(sql.strip().split('\n')))

    # dump results to hash
    h = dict()
    for row in results:
        user_handle = row[1]
        try:
            user_handle = user_handle.encode('utf-8').replace(" ", "_")
        except UnicodeDecodeError:
            user_handle = user_handle.replace(" ", "_")
        h[user_handle] = row[0]

    eligible_user_names = dl.DataLoader().cast_elems_to_string(dl.DataLoader().get_elem_from_nested_list(results,1))
    block_list = b.Blocks(date_start=str(exp_meta_data['start_date'])).process(eligible_user_names)._results

    # Replace user names with IDs
    for i in xrange(len(block_list)):
        try:
            block_list[i][0] = str(h[block_list[i][0]])
        except KeyError:
            logging.error('Cannot include %s in result.' % block_list[i][0])
            pass

    insert_buckets(block_list,users)
    create_sql = " ".join(exp_meta_data['blocks']['definition'].strip().split('\n'))
    dl.DataLoader().create_table_from_list(block_list,create_sql, exp_meta_data['blocks']['table_name'])

def edit_volume(users, period=1, log_frequency=500):
    """ Extracts bytes added and edit count "period" days after registration """
    global exp_meta_data
    logging.info("Processing bytes added for %s users." % str(len(users)))
    sql_reg_date = 'select user_registration from enwiki.user where user_id = %s;'

    bytes_added = list()
    bad_users = 0
    count = 0
    logging.info('Processing %s eligible users...' % str(len(users)))

    for user in users.keys():
        try:
            count += 1
            if not count % log_frequency: logging.info('Processed %s users, no revs for %s...' % (count, bad_users))

            start_date = date_parse(conn.execute_SQL(sql_reg_date % user)[0][0])
            end_date = start_date + datetime.timedelta(days=period)
            entry = ba.BytesAdded(date_start=start_date, date_end=end_date).process(
                user, num_threads=1).__iter__().next()
            bytes_added.append(entry)

        except Exception as e:
            # logging.error('Could not get bytes added for user %s: %s' % (str(user), e.message))
            bad_users += 1

    logging.info('Missed %s users out of %s.' % (str(bad_users), str(len(users))))
    logging.info('Writing results to table.')

    # Create table
    insert_buckets(bytes_added,users)
    sql = " ".join(exp_meta_data['edit_volume']['definition'].strip().split('\n'))
    dl.DataLoader().create_table_from_list(bytes_added, sql, exp_meta_data['edit_volume']['table_name'])

def time_to_threshold(users, first_edit=0, threshold_edit=1):
    global exp_meta_data

    logging.info("Processing time to threshold for %s users." % str(len(users)))

    # create table
    t = ttt.TimeToThreshold(ttt.TimeToThreshold.EditCountThreshold,
        first_edit=first_edit, threshold_edit=threshold_edit).process(users.keys()[:100]).__iter__()

    m_generator = insert_buckets(t,users)
    sql = " ".join(exp_meta_data['time_to_milestone']['definition'].strip().split('\n'))
    dl.DataLoader().create_table_from_list(m_generator, sql, exp_meta_data['time_to_milestone']['table_name'])

def main(args):
    global exp_meta_data
    logging.info(args)

    try: # Load the experiment meta data
        exp_meta_data = e3_def.experiments[args.experiment]
    except KeyError:
        logging.error('Experiment not found: %s'  % str(args.experiment))
        return

    # Process data
    if args.load_logs: load_logs()

    # experimental bucket value hashed on user id: {'12345' : 'acux_2', '98765' : 'control_2, ...'}
    users = dict()
    exp_meta_data['user_list'](users) # method that returns a list of user_ids and buckets
    # sql = exp_meta_data['user_list_sql']
    # user_ids = [str(row[0]) for row in conn.execute_SQL(sql)]

    if args.blocks: blocks(users)
    if args.edit_volume: edit_volume(users, period=5)
    if args.time_to_threshold: time_to_threshold(users)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="This script filters log data and build metrics from Wikimedia editor engagement experiments.",
        epilog="EXPERIMENT = %s" % str(e3_def.experiments.keys()),
        conflict_handler="resolve",
        usage = "e3_data_wrangle.py [-x EXPERIMENT] [-l] [-b] [-e] [-t] [-r]"
    )
    parser.add_argument('-x', '--experiment',type=str, help='Experiment handle.',default='CTA4')
    parser.add_argument('-l', '--load_logs',action="store_true",help='Process log data.',default=False)
    parser.add_argument('-b', '--blocks',action="store_true",help='.',default=False)
    parser.add_argument('-e', '--edit_volume',action="store_true",help='.',default=False)
    parser.add_argument('-t', '--time_to_threshold',action="store_true",help='.',default=False)
    parser.add_argument('-r', '--revert_data',action="store_true",help='.',default=False)

    args = parser.parse_args()
    sys.exit(main(args))
