import pandas as pd
import numpy as np
from nba.common import (
    data_path,
    box_score_link_table_name,
    box_score_details_table_name,
    player_detail_table_name,
    starting_rating,
    get_new_rating,
    file_lock,
    processed_team_data_table_name,
    timeit,
    processed_player_data_table_name,
    combined_feature_file_data_table_name,
    past_n_game_dataset_table_name,
    past_n_game_dataset_combined_table_name
)
import json
import copy
import pickle
from sklearn.decomposition import PCA
# import multiprocessing
from collections import Counter
import tqdm
import re
from scipy import stats
from sklearn.preprocessing import StandardScaler, QuantileTransformer


@timeit
def find_team_home_loc(df):
    home_dict = dict()
    team_tags = set(df['team_tag'])

    for i in team_tags:
        team_data = df[df['team_tag'] == i]
        team_years = set(df['year'])

        for y in team_years:
            team_year_data = team_data[team_data['year'] == y]

            c = Counter()
            for _, j in team_year_data.iterrows():
                c[j['location']] += 1
            if len(c) > 0:
                home_dict[(i, y)] = c.most_common(1)[0][0]
    return home_dict


class DataManager():

    def __init__(self, encoder_size=64, history_length=16, transpose_history_data=True, testing = False):
        self.encoder_size = encoder_size
        self.history_length = history_length
        self.transpose_history_data = transpose_history_data

        self.cache = dict()

        self.feature_indicator_str = 'feature'
        self.team_str = 'team'
        self.player_str = 'player'
        self.opponent_str = 'opponent'
        self.pregame_rating_str = 'pregame_rating'
        self.postgame_rating_str = 'postgame_rating'

        self.initial_team_data_columns = ['ast', 'ast_pct', 'blk', 'blk_pct', 'def_rtg', 'drb', 'drb_pct', 'efg_pct',
                                          'fg', 'fg3', 'fg3_pct', 'fg3a', 'fg3a_per_fga_pct', 'fg_pct', 'fga', 'ft',
                                          'ft_pct', 'fta', 'fta_per_fga_pct', 'mp', 'off_rtg', 'orb', 'orb_pct', 'pf',
                                          'plus_minus', 'pts', 'stl', 'stl_pct', 'tov', 'tov_pct', 'trb', 'trb_pct',
                                          'ts_pct', 'usg_pct', 'feature_home', 'win', 'days_since_last_fight']
        self.initial_player_data_columns = ['ast', 'ast_pct', 'blk', 'blk_pct', 'def_rtg', 'drb', 'drb_pct', 'efg_pct',
                                            'fg', 'fg3', 'fg3_pct', 'fg3a', 'fg3a_per_fga_pct', 'fg_pct', 'fga', 'ft',
                                            'ft_pct',
                                            'fta', 'fta_per_fga_pct', 'mp', 'off_rtg', 'orb', 'orb_pct', 'pf',
                                            'plus_minus', 'pts', 'stl', 'stl_pct', 'tov', 'tov_pct', 'trb', 'trb_pct',
                                            'ts_pct',
                                            'usg_pct']
        self.target = 'win'
        self.id_columns = ['team_tag', 'team_link', 'team_name', 'opponent_tag', 'opponent_name', 'opponent_link',
                           'location', 'date_str', 'game_key', 'team_game_key', 'player_link']

        self.key_columns = ['game_key', 'team_tag', 'opponent_tag', 'date_str']
        self.standalone_feature_columns = []
        self.diff_feature_cols = []

        self.testing = testing


    def update_raw_datasets(self):
        self.load_raw_data()
        self.encode_dates()
        self.assign_date_since_last_game()
        self.assign_home_for_teams()
        self.calculate_team_game_rating(0)
        self.calculate_team_game_rating(1)
        self.build_event_features()
        self.build_moving_average_features(3)
        self.build_moving_average_features(10)
        self.build_moving_average_features(50)
        self.attach_label_to_features()
        self.scale_data()
        self.save_processed_data()
        self.save_columns()
        self.save_feature_df()

    def encode_dates(self):
        self.team_data['date_dt'] = pd.to_datetime(self.team_data['date_str'], errors='coerce')
        self.team_data['dow'] = self.team_data['date_dt'].dt.dayofweek
        self.team_data['year'] = self.team_data['date_dt'].dt.year
        self.team_data['month'] = self.team_data['date_dt'].dt.month

        for e in ['dow', 'year', 'month']:
            for i in set(self.team_data[e]):
                self.team_data['{0}_{1}'.format(e, i)] = self.team_data[e].apply(lambda x: x == i).astype(int)
                self.standalone_feature_columns.append('{0}_{1}'.format(e, i))


    def load_raw_data(self):
        if self.testing:
            self.team_data = pd.read_csv('{data_path}/{db_name}.csv'.format(data_path=data_path,
                                                                            db_name=box_score_details_table_name),
                                         sep='|', low_memory=False, nrows = self.testing)
            self.player_data = pd.read_csv('{data_path}/{db_name}.csv'.format(data_path=data_path,
                                                                              db_name=player_detail_table_name), sep='|',
                                           low_memory=False, nrows = self.testing)
        else:
            self.team_data = pd.read_csv('{data_path}/{db_name}.csv'.format(data_path=data_path,
                                                                db_name=box_score_details_table_name),
                             sep='|', low_memory=False)
            self.player_data = pd.read_csv('{data_path}/{db_name}.csv'.format(data_path=data_path,
                                                                              db_name=player_detail_table_name), sep='|',
                                           low_memory=False)

        self.team_dfs_dict = dict()
        self.team_dfs_dict = dict()
        self.team_features = pd.DataFrame()
        self.player_features = pd.DataFrame()

        self.team_data['date_str'] = self.team_data.apply(
            lambda x: str(x['year']).zfill(4) + '-' + str(x['month']).zfill(2) + '-' + str(x['day']).zfill(2), axis=1)
        self.team_data['game_key'] = self.team_data.apply(
            lambda x: str(sorted([str(x['date_str']), str(x['team_tag']), str(x['opponent_tag'])])), axis=1)
        self.team_data['team_game_key'] = self.team_data.apply(
            lambda x: str([str(x['date_str']), str(x['team_tag']), str(x['opponent_tag'])]), axis=1)

        self.player_data['date_str'] = self.player_data.apply(
            lambda x: str(x['year']).zfill(4) + '-' + str(x['month']).zfill(2) + '-' + str(x['day']).zfill(2), axis=1)
        self.player_data['game_key'] = self.player_data.apply(
            lambda x: str(sorted([str(x['date_str']), str(x['team_tag']), str(x['opponent_tag'])])), axis=1)
        self.player_data['team_game_key'] = self.player_data.apply(
            lambda x: str([str(x['date_str']), str(x['team_tag']), str(x['opponent_tag'])]), axis=1)

        self.team_data = self.team_data.sort_values('date_str')
        self.player_data = self.player_data.sort_values('date_str')
        self.save_processed_data()

    @timeit
    def scale_data(self):
        self.scaler_dict = dict()
        for i in sorted(list(set(self.initial_team_data_columns + self.standalone_feature_columns + self.diff_feature_cols))):
            if i == self.target:
                continue
            if i in self.team_data.columns:
                scaler = QuantileTransformer()
                self.team_data[i] = scaler.fit_transform(self.team_data[i].fillna(self.team_data[i].median()).values.reshape(-1, 1))
                self.scaler_dict[(i, 'team_data')] = scaler
            if i in self.feature_df.columns:
                scaler = QuantileTransformer()
                self.feature_df[i] = scaler.fit_transform(self.feature_df[i].fillna(self.feature_df[i].median()).values.reshape(-1, 1))
                self.scaler_dict[(i, 'feature_df')] = scaler

    @timeit
    def build_timeseries(self, history_length, transpose_history_data):
        print(f'build_timeseries: {history_length} {transpose_history_data}')
        self.load_processed_data()
        self.load_columns()
        teams = set(self.team_data['team_tag'])

        team_data_opponent = self.team_data.copy()

        team_data_opponent['temp_column'] = team_data_opponent['team_tag']
        team_data_opponent['team_tag'] = team_data_opponent['opponent_tag']
        team_data_opponent['opponent_tag'] = team_data_opponent['temp_column']
        team_data_opponent['game_key'] = team_data_opponent.apply(
            lambda x: str(sorted([str(x['date_str']), str(x['team_tag']), str(x['opponent_tag'])])), axis=1)
        team_data_opponent['team_game_key'] = team_data_opponent.apply(
            lambda x: str([str(x['date_str']), str(x['team_tag']), str(x['opponent_tag'])]), axis=1)

        opponent_columns = list()
        for i in self.initial_team_data_columns:
            team_data_opponent['{}_opponent'.format(i)] = team_data_opponent[i]
            opponent_columns.append('{}_opponent'.format(i))

        self.team_data = self.team_data.merge(
            team_data_opponent[['team_tag', 'opponent_tag', 'game_key'] + opponent_columns])
        past_n_game_dataset = dict()
        temp_team_df_dict = dict()
        for t in tqdm.tqdm(teams):
            temp_team_df_dict[t] = self.team_data[self.team_data['team_tag'] == t]

        combined_columns = self.initial_team_data_columns + opponent_columns

        for t in tqdm.tqdm(teams):
            past_n_game_dataset[t] = dict()
            temp_team_df_dict[t] = temp_team_df_dict[t].sort_values('date_str')

            game_ids = set(temp_team_df_dict[t]['game_key'])
            temp_team_df_dict[t] = temp_team_df_dict[t].set_index('game_key')

            for g in game_ids:
                g_iloc = temp_team_df_dict[t].index.get_loc(g)
                pregame_matrix = temp_team_df_dict[t].shift(history_length).iloc[g_iloc:g_iloc + history_length][
                    combined_columns].fillna(0).values

                while pregame_matrix.shape[0] < history_length:
                    new_array = np.array([[0 for _ in combined_columns]])
                    pregame_matrix = np.vstack([new_array, pregame_matrix])

                diff = pregame_matrix[:, 0:len(self.initial_team_data_columns)] - pregame_matrix[:,
                                                                                  len(self.initial_team_data_columns):]
                pregame_matrix = np.hstack([pregame_matrix, diff])

                if transpose_history_data:
                    pregame_matrix = pregame_matrix.transpose()

                past_n_game_dataset[t][g] = pregame_matrix

        self.save_past_n_game_dataset(past_n_game_dataset, history_length, transpose_history_data)

    @timeit
    def combine_timeseries(self, history_length, transpose_history_data):
        self.load_processed_data()
        self.load_columns()
        all_keys = self.team_data[['game_key', 'team_tag', 'opponent_tag', 'date_str']]
        all_keys = all_keys.drop_duplicates()
        past_n_game_dataset = self.load_past_n_game_dataset(history_length, transpose_history_data)

        past_n_game_datasets_combined = dict()
        for _, row in all_keys.iterrows():
            past_n_game_datasets_combined.setdefault(row['team_tag'], dict())
            team_record = past_n_game_dataset[row['team_tag']][row['game_key']]
            opponent_record = past_n_game_dataset[row['opponent_tag']][row['game_key']]
            diff = team_record - opponent_record
            past_n_game_datasets_combined[row['team_tag']][row['game_key']] = np.hstack([team_record,
                                                                                         opponent_record,
                                                                                         diff])
        self.save_past_n_game_dataset_combined(past_n_game_datasets_combined, history_length, transpose_history_data)

    @timeit
    def build_event_features(self):
        all_keys = self.team_data[sorted(list(set(self.standalone_feature_columns + self.key_columns + self.diff_feature_cols)))]
        all_keys = all_keys.drop_duplicates()

        rows_dicts = dict()
        for _, row in all_keys.iterrows():
            rows_dicts[(row['game_key'], row['team_tag'], row['opponent_tag'])] = row[self.standalone_feature_columns]

        results = list()
        for _, row in all_keys.iterrows():
            next_dict = dict()

            row = row.fillna(0)
            for i in sorted(list(set(self.standalone_feature_columns + self.key_columns))):
                next_dict[i] = row[i]

            record_o = rows_dicts[(row['game_key'], row['opponent_tag'], row['team_tag'])].fillna(0)
            for i in self.diff_feature_cols:
                r1 = row[i]
                r2 = record_o[i]
                next_dict['{}_diff'.format(i)] = r1 - r2

            results.append(next_dict)
        self.feature_df = pd.DataFrame.from_dict(results)
        print(self.feature_df.columns.tolist())

    @timeit
    def attach_label_to_features(self):
        team_features = self.team_data.copy()
        self.feature_df = self.feature_df.merge(team_features[['team_tag', 'opponent_tag', 'game_key', 'date_str', self.target]])

    @timeit
    def build_moving_average_features(self, n):
        team_features = self.team_data.copy()
        teams = set(team_features['team_tag'])
        new_features = set()

        def get_slope(s):
            slope, _, _, _, _ = stats.linregress(list(range(s.shape[0])), s.tolist())
            return slope

        for t in tqdm.tqdm(teams):
            for c in self.initial_team_data_columns:
                col_name1= f'{self.feature_indicator_str }_{self.team_str}_rl_avg_{c}_{n}'
                col_name2= f'{self.feature_indicator_str }_{self.team_str}_rl_trend_{c}_{n}'

                team_features.loc[team_features['team_tag'] == t, col_name1] = self.team_data[self.team_data['team_tag'] == t].shift(periods=1).rolling(window=n)[c].mean()
                new_features.add(col_name1)

                team_features.loc[team_features['team_tag'] == t, col_name2] = self.team_data[self.team_data['team_tag'] == t].shift(periods=1).rolling(window=n)[c].apply(get_slope)
                new_features.add(col_name2)

        new_features = list(new_features)
        self.feature_df = self.feature_df.merge(team_features[new_features + ['team_tag', 'opponent_tag', 'game_key', 'date_str']])
        self.standalone_feature_columns.extend(new_features)
        self.diff_feature_cols.extend(new_features)
        print(self.feature_df.columns.tolist())


    @timeit
    def get_labeled_data(self, history_length = None, transpose_history_data = None, get_history_data = True):
        self.load_processed_data()
        self.load_feature_df()
        self.load_columns()
        if get_history_data:
            past_n_game_dataset_combined = self.load_past_n_game_dataset_combined(history_length, transpose_history_data)

        all_features = self.standalone_feature_columns.copy()

        for i in self.diff_feature_cols:
            all_features.append('{}_diff'.format(i))

        print(self.feature_df.columns.tolist())
        y, x1, x2 = [], [], []
        for _, row in self.feature_df.iterrows():
            y.append(row[self.target])
            row = row.fillna(0)
            if get_history_data:
                x1.append(past_n_game_dataset_combined[row['team_tag']][row['game_key']])
            x2.append(row[all_features])

        return np.array(x1), np.array(x2), np.array(y)

    @timeit
    def assign_home_for_teams(self):
        home_dict = find_team_home_loc(self.team_data)
        self.team_data['feature_home'] = self.team_data.apply(
            lambda x: 1 if home_dict[(x['team_tag'], x['year'])] == x['location'] else 0, axis=1)
        self.standalone_feature_columns.append('feature_home')

    @timeit
    def assign_date_since_last_game(self):
        self.team_data['date_dt'] = pd.to_datetime(self.team_data['date_str'], errors='coerce')
        self.team_data['days_since_last_fight'] = self.team_data.groupby('team_tag')['date_dt'].diff()
        self.team_data['days_since_last_fight'] = self.team_data['days_since_last_fight'].dt.days
        self.standalone_feature_columns.append('days_since_last_fight')
        self.diff_feature_cols.append('days_since_last_fight')


    @timeit
    def calculate_team_game_rating(self, rating_type):
        team_data_copy = self.team_data.sort_values('date_str').copy()
        'feature_team_pregame_rating_0'
        new_col_pre = f'feature_team_pregame_rating_{rating_type}'
        new_col_post = f'feature_team_postgame_rating_{rating_type}'
        self.diff_feature_cols.append(new_col_pre)
        self.standalone_feature_columns.append(new_col_pre)

        team_data_copy = team_data_copy[['team_tag', 'opponent_tag', 'date_str']]
        team_data_copy[new_col_pre] = None
        team_data_copy[new_col_post] = None

        for i, r in tqdm.tqdm(self.team_data.iterrows()):
            team_previous_record = self.get_most_recent_team_record_before_date(team_data_copy, r['team_tag'],
                                                                                r['date_str'])
            opponent_previous_record = self.get_most_recent_team_record_before_date(team_data_copy, r['opponent_tag'],
                                                                                    r['date_str'])

            if team_previous_record.empty:
                team_previous_rating = starting_rating
            else:
                team_previous_rating = team_previous_record[new_col_post]

            if opponent_previous_record.empty:
                opponent_previous_rating = starting_rating
            else:
                opponent_previous_rating = opponent_previous_record[new_col_post]
            team_data_copy.loc[i, new_col_pre] = team_previous_rating
            team_data_copy.loc[i, new_col_post] = get_new_rating(
                team_previous_rating,
                opponent_previous_rating,
                r['win'], multiplier=1, rating_type=rating_type)

        self.team_data = self.team_data.merge(team_data_copy)

    #################################################################################################################
    # Helper methods

    def presplit_teams_and_players(self):
        for team_tag in set(self.team_data['team_tag']):
            self.team_dfs_dict[team_tag] = self.team_data[self.team_data['team_tag']]
            self.team_dfs_dict[team_tag] = self.team_dfs_dict[team_tag].sort_values('date_str')

        for player_link in set(self.player_data['player_link']):
            self.player_data[player_link] = self.player_data[self.player_data['player_link']]
            self.player_data[player_link] = self.player_data[player_link].sort_values('date_str')

    def get_most_recent_team_record_before_date(self, df, tag, date_str):
        sub_df = df[(df['date_str'] < date_str) & (df['team_tag'] == tag)]
        if not sub_df.empty:
            return sub_df.iloc[-1]
        return sub_df

    def get_team_record_right_after_date(self, df, tag, date_str):
        sub_df = df[(df['date_str'] > date_str) & (df['team_tag'] == tag)]
        if not sub_df.empty:
            return sub_df.iloc[-1]
        return sub_df

    def get_most_recent_player_record_before_date(self, df, tag, date_str):
        sub_df = df[(df['date_str'] < date_str) & (df['player_link'] == tag)]
        if not sub_df.empty:
            return sub_df.iloc[-1]
        return sub_df

    def get_player_record_right_after_date(self, df, tag, date_str):
        sub_df = df[(df['date_str'] > date_str) & (df['player_link'] == tag)]
        if not sub_df.empty:
            return sub_df.iloc[-1]
        return sub_df

    @timeit
    def save_past_n_game_dataset_combined(self, past_n_game_datasets_combined, history_length, transpose_history_data):
        with open(
                f'{data_path}/{past_n_game_dataset_combined_table_name}_{history_length}_{transpose_history_data}.pkl',
                'wb') as f:
            pickle.dump(past_n_game_datasets_combined, f)

    @timeit
    def load_past_n_game_dataset_combined(self, history_length, transpose_history_data):
        with open(
                f'{data_path}/{past_n_game_dataset_combined_table_name}_{history_length}_{transpose_history_data}.pkl',
                'rb') as f:
            return pickle.load(f)

    @timeit
    def save_past_n_game_dataset(self, past_n_game_dataset, history_length, transpose_history_data):
        with open(f'{data_path}/{past_n_game_dataset_table_name}_{history_length}_{transpose_history_data}.pkl',
                  'wb') as f:
            pickle.dump(past_n_game_dataset, f)

    @timeit
    def load_past_n_game_dataset(self, history_length, transpose_history_data):
        with open(f'{data_path}/{past_n_game_dataset_table_name}_{history_length}_{transpose_history_data}.pkl',
                  'rb') as f:
            return pickle.load(f)

    def save_processed_data(self):
        self.team_data.to_csv('{data_path}/{db_name}_processed.csv'.format(data_path=data_path,
                                                                           db_name=box_score_details_table_name),
                              sep='|', index=False)
        self.player_data.to_csv('{data_path}/{db_name}_processed.csv'.format(data_path=data_path,
                                                                             db_name=player_detail_table_name), sep='|',
                                index=False)

    def load_processed_data(self):
        self.team_data = pd.read_csv('{data_path}/{db_name}_processed.csv'.format(data_path=data_path,
                                                                                  db_name=box_score_details_table_name),
                                     sep='|', low_memory=False)
        self.player_data = pd.read_csv('{data_path}/{db_name}_processed.csv'.format(data_path=data_path,
                                                                                    db_name=player_detail_table_name),
                                       sep='|', low_memory=False)

    def save_columns(self):
        with open('{data_path}/{db_name}.json'.format(data_path=data_path, db_name='key_columns'), 'w') as f:
            json.dump(self.key_columns, f)
        with open('{data_path}/{db_name}.json'.format(data_path=data_path, db_name='standalone_feature_columns'), 'w') as f:
            json.dump(self.standalone_feature_columns, f)
        with open('{data_path}/{db_name}.json'.format(data_path=data_path, db_name='diff_feature_cols'), 'w') as f:
            json.dump(self.diff_feature_cols, f)
        with open('{data_path}/{db_name}.json'.format(data_path=data_path, db_name='initial_team_column_list'), 'w') as f:
            json.dump(self.initial_team_data_columns, f)
        with open('{data_path}/{db_name}.json'.format(data_path=data_path, db_name='initial_player_column_list'), 'w') as f:
            json.dump(self.initial_player_data_columns, f)

    def load_columns(self):
        with open('{data_path}/{db_name}.json'.format(data_path=data_path, db_name='key_columns'), 'r') as f:
            self.key_columns = json.load(f)
        with open('{data_path}/{db_name}.json'.format(data_path=data_path, db_name='standalone_feature_columns'), 'r') as f:
            self.standalone_feature_columns = json.load(f)
        with open('{data_path}/{db_name}.json'.format(data_path=data_path, db_name='diff_feature_cols'), 'r') as f:
            self.diff_feature_cols = json.load(f)
        with open('{data_path}/{db_name}.json'.format(data_path=data_path, db_name='initial_team_column_list'), 'r') as f:
            self.initial_team_data_columns = json.load(f)
        with open('{data_path}/{db_name}.json'.format(data_path=data_path, db_name='initial_player_column_list'), 'r') as f:
            self.initial_player_data_columns = json.load(f)

    def save_feature_df(self):
        self.feature_df.to_csv('{data_path}/{db_name}.csv'.format(data_path=data_path,
                                                                           db_name='features'),
                              sep='|', index=False)
        print('save_feature_df: {}'.format(self.feature_df.columns.tolist()))

    def load_feature_df(self):
        self.feature_df = pd.read_csv('{data_path}/{db_name}.csv'.format(data_path=data_path,
                                                                           db_name='features'),
                                     sep='|', low_memory=False)
        print('load_feature_df: {}'.format(self.feature_df.columns.tolist()))


def create_data_files():
    dm = DataManager()
    dm.update_raw_datasets()
    dm.build_timeseries(4, False)
    dm.combine_timeseries(4, False)
    x1, x2, y = dm.get_labeled_data(4, False)
    print(x1.shape, x2.shape, y.shape)

if __name__ == '__main__':
    create_data_files()
