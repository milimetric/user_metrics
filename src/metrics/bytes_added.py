
__author__ = "Ryan Faulkner"
__date__ = "July 27th, 2012"
__license__ = "GPL (version 2 or later)"

import multiprocessing as mp
import datetime
import user_metric as um
import math
import os

class BytesAdded(um.UserMetric):
    """
        Produces a float value that reflects the rate of edit behaviour

            `https://meta.wikimedia.org/wiki/Research:Metrics/edit_rate`

            usage e.g.: ::

                >>> import classes.Metrics as M
                >>> M.BytesAdded(date_start='2012-07-30 00:00:00', raw_count=False, mode=1).process(123456)
                5
                1200
    """

    @staticmethod
    def __doc__():
        return 'process-**kwargs:\n"log_frequency" frequency to log to stdout.' \
               '\n"log_progress" log metrics gathering.'

    def __init__(self,
                 date_start='2010-01-01 00:00:00',
                 date_end=datetime.datetime.now(),
                 project='enwiki',
                 **kwargs):

        """
            - Parameters:
                - **date_start**: string or datetime.datetime. start date of edit interval
                - **date_end**: string or datetime.datetime. end date of edit interval
                - **raw_count**: Boolean. Flag that when set to True returns one total count for all users.
                                            Count by user otherwise.

            - Return:
                - Empty.
        """
        self._start_ts_ = self._get_timestamp(date_start)
        self._end_ts_ = self._get_timestamp(date_end)
        um.UserMetric.__init__(self, project=project, **kwargs)

    @staticmethod
    def header(): return ['user_id', 'bytes_added_net', 'bytes_added_absolute',
                          'bytes_added_pos', 'bytes_added_neg', 'edit_count']

    def process(self, user_handle=None, is_id=True, **kwargs):
        """ Setup metrics gathering using multiprocessing """

        k = kwargs['num_threads'] if 'num_threads' in kwargs else 1
        kwargs['is_id'] = is_id
        kwargs['start_ts'] = self._start_ts_
        kwargs['end_ts'] = self._end_ts_
        kwargs['project'] = self._project_

        if user_handle:
            if not hasattr(user_handle, '__iter__'): user_handle = [user_handle]

        # Multiprocessing vs. single processing execution
        if k:
            # build the argument lists for each thread
            if not user_handle:
                sql = 'select distinct rev_user from enwiki.revision where rev_timestamp >= "%s" and rev_timestamp < "%s"'
                sql = sql % (self._start_ts_, self._end_ts_)
                print str(datetime.datetime.now()) + ' - Getting all distinct users: " %s "' % sql
                user_handle = [str(row[0]) for row in self._data_source_.execute_SQL(sql)]
                print str(datetime.datetime.now()) + ' - Retrieved %s users.' % len(user_handle)

            n = int(math.ceil(float(len(user_handle)) / k))
            arg_list = [[user_handle[i * n : (i + 1) * n], kwargs] for i in xrange(k)]
            arg_list = filter(lambda x: len(x[0]), arg_list) # remove any args with empty user handle lists

            pool = mp.Pool(processes=len(arg_list))
            self._results = list()
            for elem in pool.map(_process_help, arg_list): self._results.extend(elem)
        else:
            self._results = _process_help([user_handle, kwargs])
        return self

def _process_help(args):

    """
        Determine the bytes added over a number of revisions for user(s).  The parameter *user_handle* can
        be either a string or an integer or a list of these types.  When the *user_handle* type is integer it is
        interpreted as a user id, and as a user_name for string input.  If a list of users is passed to the
        *process* method then a dict object with edit rates keyed by user handles is returned.

        The flow of the request is as follows:

            #. Get all revisions for the specified users in the given timeframe
            #. For each parent revision get its length
            #. Compute the difference in length between each revision and its parent
            #. Record edit count, raw bytes added (with sign and absolute), amount of positive bytes added,
            amount of negative bytes added

        - Parameters:
            - **user_handle** - String or Integer (optionally lists).  Value or list of values representing
                                user handle(s).
            - **is_id** - Boolean.  Flag indicating whether user_handle stores user names or user ids

        - Return:
            - Dictionary. key(string): user handle, value(Float): edit counts
    """

    conn = um.dl.Connector(instance='slave')
    user_handle = args[0]
    key_word_args = args[1]

    # Extract kwargs
    is_log = bool(key_word_args['log_progress']) if 'log_progress' in key_word_args else False
    freq = int(key_word_args['log_frequency']) if 'log_frequency' in key_word_args else 0
    is_id = bool(key_word_args['is_id']) if 'is_id' in key_word_args else True
    start_ts = str(key_word_args['start_ts']) if 'start_ts' in key_word_args else '20120101000000'
    end_ts = str(key_word_args['end_ts']) if 'end_ts' in key_word_args else str(datetime.datetime.now())
    project = str(key_word_args['project']) if 'project' in key_word_args else 'enwiki'

    bytes_added = dict()
    ts_condition  = 'rev_timestamp >= "%s" and rev_timestamp < "%s"' % (start_ts, end_ts)

    # determine the format field
    field_name = ['rev_user_text','rev_user'][is_id]

    # build the user set for inclusion into the query - if the user_handle is empty or None get all users for timeframe
    if user_handle:
        user_handle = um.UserMetric._escape_var(user_handle) # Escape user_handle for SQL injection
        if not hasattr(user_handle, '__iter__'): user_handle = [user_handle] # ensure the handles are iterable
        if is_id:
            user_set = um.dl.DataLoader().format_comma_separated_list(user_handle, include_quotes=False)
        else:
            user_set = um.dl.DataLoader().format_comma_separated_list(user_handle, include_quotes=True)
        where_clause = '%(field_name)s in (%(user_set)s) and %(ts_condition)s' % {
        'field_name' : field_name, 'user_set' : user_set, 'ts_condition' : ts_condition}
    else:
        where_clause = '%(ts_condition)s' % {'ts_condition' : ts_condition}

    sql = """
            select
                %(field_name)s,
                rev_len,
                rev_parent_id
            from %(project)s.revision
            where %(where_clause)s
        """ % {
            'field_name' : field_name,
            'where_clause' : where_clause,
            'project' : project}
    sql = " ".join(sql.strip().split())

    if is_log:
        print str(datetime.datetime.now()) + \
              ' - Querying revisions for %s users ... (PID = %s)' % (len(user_handle),os.getpid())
    try:
        results = conn.execute_SQL(sql)
    except um.MySQLdb.ProgrammingError:
        raise um.UserMetric.UserMetricError(message=str(BytesAdded) +
                                           '::Could not get bytes added for specified users(s) - Query Failed.')

    # Get the difference for each revision length from the parent to compute bytes added
    row_count = 1
    missed_records = 0
    total_rows = len(results)

    if is_log: print str(datetime.datetime.now()) + ' - Processing revision data by user... (PID = %s)' % os.getpid()
    for row in results:
        try:
            user = str(row[0])
            rev_len_total = int(row[1])
            parent_rev_id = row[2]

        except IndexError:
            missed_records += 1
            continue
        except TypeError:
            missed_records += 1
            continue

        # Produce the revision length of the parent
        if parent_rev_id == 0: # In case of a new article, parent_rev_id = 0, no record in the db
            parent_rev_len = 0
        else:
            sql = 'select rev_len from enwiki.revision where rev_id = %(parent_rev_id)s' % {
                  'parent_rev_id' : parent_rev_id}
            try:
                parent_rev_len = conn.execute_SQL(sql)[0][0]
            except IndexError:
                missed_records += 1
                continue
            except TypeError:
                missed_records += 1
                continue
            except um.MySQLdb.ProgrammingError:
                raise um.UserMetric.UserMetricError(message=str(BytesAdded) +
                            '::Could not produce rev diff for %s on rev_id %s.' % (user, str(parent_rev_id)))

        # Update the bytes added hash - ignore revision if either rev length is undetermined
        try:
            bytes_added_bit = int(rev_len_total) - int(parent_rev_len)
        except TypeError:
            missed_records += 1
            continue

        try: # Exception where the user does not exist.  Handle this by creating the key
            bytes_added[user][0] += bytes_added_bit
        except KeyError:
            bytes_added[user] = [0] * 5
            bytes_added[user][0] += bytes_added_bit
            pass

        bytes_added[user][1] += abs(bytes_added_bit)
        if bytes_added_bit > 0:
            bytes_added[user][2] += bytes_added_bit
        else:
            bytes_added[user][3] += bytes_added_bit
        bytes_added[user][4] += 1


        if freq and row_count % freq == 0 and is_log:
            s = ' - Processed %s of %s records. (PID = %s)' % (row_count, total_rows, os.getpid())
            print str(datetime.datetime.now()) + s

        row_count += 1

    results = [[user] + bytes_added[user] for user in bytes_added]
    if is_log:
        s = ' - processed %s out of %s records. (PID = %s)' % (total_rows - missed_records,total_rows, os.getpid())
        print str(datetime.datetime.now()) + s

    return results

# Used for testing
if __name__ == "__main__":
    BytesAdded(date_start='20120101000000',date_end='20121101000000').process(user_handle=['156171','13234584'],
        log_progress=True, log_frequency=10)